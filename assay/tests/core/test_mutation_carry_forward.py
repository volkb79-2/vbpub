"""B561/WP3a: earlier-judge carry-forward lookup and conservative outcomes."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from assay.cli import _resolve_carry_forward_sources, main
from assay.errors import LaneConfigError
from assay.resource_limits import ResourceLimitCounters
from conftest import GitRepo
from test_flex_r2_campaigns import _RUN_GATE, _event, _run

from assay import mutation

_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "tests/judge.sh"]
env = {}
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
_METHOD = "def flag():\n    return True\n"
_BLIND = "exit 0\n"
_KILL_METHOD = "grep -q 'return False' pkg/flags.py && exit 1\nexit 0\n"


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


def _seed_method(repo: GitRepo, judge_script: str) -> None:
    repo.write(".gitignore", ".assay/\n__pycache__/\n.pytest_cache/\n")
    repo.write("assay.toml", _LANE)
    repo.write("run-gate.toml", _RUN_GATE)
    repo.write("pkg/flags.py", _METHOD.replace("True", "False"))
    repo.write("tests/judge.sh", judge_script)
    repo.commit_all("lane and base source")
    repo.git("checkout", "-q", "-b", "base")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", _METHOD)
    repo.commit_all("restore current method")


def _seed_source(repo: GitRepo, current: str, base: str) -> None:
    repo.write(".gitignore", ".assay/\n__pycache__/\n.pytest_cache/\n")
    repo.write("assay.toml", _LANE)
    repo.write("run-gate.toml", _RUN_GATE)
    repo.write("pkg/flags.py", current)
    repo.write("tests/judge.sh", _BLIND)
    repo.commit_all("lane and initial source")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", base)
    repo.commit_all("base source")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", current)
    repo.commit_all("restore current source")


def _job(source: str, *, path: str = "pkg/flags.py") -> mutation.MutantJob:
    raw = source.encode("utf-8")
    start = raw.index(b"True")
    return mutation.MutantJob(
        path=path,
        original_text=source,
        site=mutation.MutationSite(
            start_byte=start,
            end_byte=start + len(b"True"),
            replacement=b"False",
            lineno=source[:start].count("\n") + 1,
            operator="python:bool-const-flip",
            description="True->False",
        ),
    )


def _minimal_record(
    job: mutation.MutantJob,
    *,
    candidate: str | None = None,
    judge: str,
    bucket: str,
) -> dict:
    fields = mutation._locus_fields_for_job(job)
    record = {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        **mutation.candidate_identity_fields(job),
        "candidate_id": candidate or mutation.candidate_id(job),
        "judge_sha256": judge,
        "replacement_sha256": job.site.replacement_sha256,
        "outcome_bucket": bucket,
        "lineno": job.site.lineno,
        "description": job.site.description,
    }
    record.update(fields)
    return record


def _write_records(root: Path, *records: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for record in records:
        (root / f"{record['candidate_id']}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )


def _records(root: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.json"))
    ]


def _plan(repo: GitRepo, *extra: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = main(
        ["plan", "unit", "--file", str(repo.path / "assay.toml"), *extra],
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def _invoke_run(
    repo: GitRepo, state: Path, progress: Path, *extra: str
) -> tuple[int, str]:
    err = io.StringIO()
    code = main(
        [
            "run",
            "unit",
            "--file",
            str(repo.path / "assay.toml"),
            "--state-dir",
            str(state),
            "--progress",
            str(progress),
            "--resume",
            *extra,
        ],
        stdout=io.StringIO(),
        stderr=err,
    )
    return code, err.getvalue()


def _run_text(
    repo: GitRepo,
    state: Path | None,
    progress: Path,
    *extra: str,
    verdict: Path | None = None,
) -> tuple[int, str, str, list[dict]]:
    out, err = io.StringIO(), io.StringIO()
    argv = [
        "run", "unit", "--file", str(repo.path / "assay.toml"),
        "--progress", str(progress), "--resume",
        *(["--state-dir", str(state)] if state is not None else ()),
        *(["--verdict-json", str(verdict)] if verdict is not None else ()),
        *extra,
    ]
    code = main(argv, stdout=out, stderr=err)
    events = (
        [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
        if progress.exists()
        else []
    )
    return code, out.getvalue(), err.getvalue(), events


def test_locus_kill_is_queued_for_a_full_run_and_never_carried_as_killed(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _KILL_METHOD)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    assert _event(first, "candidate")["outcome_bucket"] == "killed"

    # An edit outside the function changes the candidate id but preserves A'.
    git_repo.write("pkg/flags.py", _METHOD + "\nOTHER = 1\n")
    git_repo.commit_all("edit outside the method")
    probe_state = tmp_path / "probe-state"
    _code, probe = _run(git_repo, probe_state, tmp_path / "probe.jsonl")
    current_judge = _event(probe, "candidates")["judge_sha256"]

    old_record_path = next(source_store.glob("*.json"))
    prior = json.loads(old_record_path.read_text(encoding="utf-8"))
    assert prior["judge_sha256"] != current_judge

    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
    )
    carry = _event(final, "carry_forward")
    assert carry["locus"] == 1
    assert carry["kills_requeued"] == 1
    assert carry["from_judges"] == 1
    assert carry["reason"] == "carry-forward"
    assert carry["sources"] == [str(source_store)]
    candidates = [event for event in final if event.get("event") == "candidate"]
    assert len(candidates) == 1
    assert candidates[0]["outcome_bucket"] == "killed"
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["outcome_bucket"] == "killed"
    assert "provenance" not in record
    assert "carry_over" not in record
    assert code == 0


def test_survivor_is_carried_pessimistically_and_keeps_the_r2_claim_failing(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    assert _event(first, "candidate")["outcome_bucket"] == "survived"
    judge = _event(first, "candidates")["judge_sha256"]
    (prior_path,) = source_store.glob("*.json")
    prior_record = json.loads(prior_path.read_text(encoding="utf-8"))
    git_repo.write("pkg/other.py", "VALUE = 1\n")
    git_repo.commit_all("change the judged tree outside the candidate")

    verdict_path = tmp_path / "carried-verdict.json"
    code, out, err, final = _run_text(
        git_repo,
        None,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
        verdict=verdict_path,
    )
    carry = _event(final, "carry_forward")
    assert carry["exact_id"] == 1
    assert carry["survivors_carried"] == 1
    assert carry["from_judges"] == 1
    assert _event(final, "candidate") is None
    final_state = mutation.default_state_root(git_repo.path)
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["judge_sha256"] == judge
    assert set(record["carry_over"]) == mutation._CARRY_OVER_V2_KEYS
    assert record["carry_over"]["into_judge_sha256"] != judge
    assert record["outcome_bucket"] == "survived"
    assert record["carry_over"]["schema_version"] == 2
    assert record["carry_over"]["reason"] == "carry-forward"
    assert record["provenance"] == {
        "kind": "carried",
        "from_candidate_id": prior_record["candidate_id"],
        "from_judge_sha256": judge,
        "match": "exact-id",
        "reason": mutation.CARRY_FORWARD_REASON,
    }
    assert mutation.record_provenance(record) == record["provenance"]
    assert (
        mutation.record_effective_judge(record)
        == record["carry_over"]["into_judge_sha256"]
    )
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert main(
        ["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0
    r2 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R2")
    assert r2["status"] == "FAIL"
    assert code == 1, (out, err)
    assert out.count("--rejudge-carried") == 1
    assert "WP4 replay" in out


def test_carry_forward_records_the_outcome_without_progress(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, _first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    git_repo.write("tests/judge.sh", _BLIND + "# whole-tree judge change\n")
    git_repo.commit_all("judge change")
    final_state = tmp_path / "final-state"
    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(final_state),
            "--resume",
            "--carry-forward",
            str(source_store),
        ],
        stdout=out,
        stderr=err,
    )
    assert code == 1
    (record,) = _records(final_state)
    assert record["provenance"]["kind"] == "carried"
    assert "provenance" in record and "carry_over" in record


def test_exact_id_prior_judge_kill_is_queued_for_full_run(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _KILL_METHOD)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    candidate = _event(first, "candidate")
    assert candidate["outcome_bucket"] == "killed"
    prior_judge = _event(first, "candidates")["judge_sha256"]
    git_repo.write("pkg/other.py", "VALUE = 1\n")
    git_repo.commit_all("change the judged tree outside the candidate")

    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
    )
    carry = _event(final, "carry_forward")
    assert carry["exact_id"] == 1
    assert carry["kills_requeued"] == 1
    assert carry["from_judges"] == 1
    assert _event(final, "candidate")["outcome_bucket"] == "killed"
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["outcome_bucket"] == "killed"
    assert "provenance" not in record
    assert "carry_over" not in record
    assert record["judge_sha256"] != prior_judge
    assert code == 0


def test_same_judge_exact_record_is_left_to_merge_state(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _KILL_METHOD)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    assert _event(first, "candidate")["outcome_bucket"] == "killed"

    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--merge-state",
        str(source_store),
        "--carry-forward",
        str(source_store),
    )
    carry = _event(final, "carry_forward")
    assert carry["same_judge_resume"] == 1
    assert carry["exact_id"] == 0
    assert carry["kills_requeued"] == 0
    assert _event(final, "candidate") is None
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["outcome_bucket"] == "killed"
    assert "carry_over" not in record
    assert code == 0


def test_same_judge_locus_record_is_not_a_cross_judge_match(tmp_path: Path):
    job = _job("def flag():\n    return True\n")
    prior = _job("def flag():\n    return True\n\nOTHER = 1\n")
    judge = "a" * 64
    store = tmp_path / "prior"
    _write_records(
        store,
        _minimal_record(prior, judge=judge, bucket="survived"),
    )

    matches, ambiguous = mutation._carry_forward_lookup(
        [job], (store,), judge=judge
    )
    assert matches == {}
    assert ambiguous == 0


def test_locus_survivor_is_rewritten_for_the_current_candidate(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    old_id = _event(first, "candidate")["candidate_id"]

    git_repo.write("pkg/flags.py", _METHOD + "\nOTHER = 1\n")
    git_repo.commit_all("edit outside the method")

    final_state = tmp_path / "final-state"
    verdict_path = tmp_path / "carried-verdict.json"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
        verdict=verdict_path,
    )
    carry = _event(final, "carry_forward")
    assert carry["locus"] == 1
    assert carry["survivors_carried"] == 1
    assert _event(final, "candidate") is None
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["candidate_id"] != old_id
    assert record["provenance"] == {
        "kind": "carried",
        "from_candidate_id": old_id,
        "from_judge_sha256": _event(first, "candidates")["judge_sha256"],
        "match": "locus",
        "reason": mutation.CARRY_FORWARD_REASON,
    }
    assert record["outcome_bucket"] == "survived"
    r2 = next(
        claim
        for claim in json.loads(verdict_path.read_text(encoding="utf-8"))["claims"]
        if claim["rigor"] == "R2"
    )
    assert r2["status"] == "FAIL"
    assert code == 1


def test_chained_carry_forward_names_the_original_executed_record(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    first_state = git_repo.path / ".assay" / "first"
    _code, first = _run(git_repo, first_state, tmp_path / "first.jsonl")
    original = _event(first, "candidate")
    original_judge = _event(first, "candidates")["judge_sha256"]

    git_repo.write("pkg/flags.py", _METHOD + "\nOTHER = 1\n")
    git_repo.commit_all("first source edit")
    second_state = git_repo.path / ".assay" / "second"
    _code, _second = _run(
        git_repo,
        second_state,
        tmp_path / "second.jsonl",
        "--carry-forward",
        str(first_state),
    )
    (second,) = _records(second_state)
    assert second["candidate_id"] != original["candidate_id"]
    assert second["provenance"]["from_candidate_id"] == original["candidate_id"]

    git_repo.write("pkg/flags.py", _METHOD + "\nOTHER = 1\nSECOND = 2\n")
    git_repo.commit_all("second source edit")
    third_state = tmp_path / "third"
    _code, _third = _run(
        git_repo,
        third_state,
        tmp_path / "third.jsonl",
        "--carry-forward",
        str(second_state),
    )
    (third,) = _records(third_state)
    assert third["candidate_id"] != second["candidate_id"]
    assert third["provenance"]["from_candidate_id"] == original["candidate_id"]
    assert third["provenance"]["from_judge_sha256"] == original_judge


def test_locus_carry_stays_locus_across_a_judge_only_change(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    first_state = git_repo.path / ".assay" / "first"
    _code, first = _run(git_repo, first_state, tmp_path / "first.jsonl")
    original = _event(first, "candidate")
    original_judge = _event(first, "candidates")["judge_sha256"]

    git_repo.write("pkg/flags.py", _METHOD + "\nOTHER = 1\n")
    git_repo.commit_all("source edit outside the site")
    second_state = git_repo.path / ".assay" / "second"
    _code, _second = _run(
        git_repo,
        second_state,
        tmp_path / "second.jsonl",
        "--carry-forward",
        str(first_state),
    )
    (second,) = _records(second_state)
    assert second["provenance"]["match"] == "locus"
    assert second["provenance"]["from_candidate_id"] == original["candidate_id"]
    assert second["provenance"]["from_judge_sha256"] == original_judge

    git_repo.write("tests/judge.sh", _BLIND + "# judge-only change\n")
    git_repo.commit_all("change only the judge")
    third_state = tmp_path / "third"
    _code, third = _run(
        git_repo,
        third_state,
        tmp_path / "third.jsonl",
        "--carry-forward",
        str(second_state),
    )
    assert _event(third, "carry_forward")["survivors_carried"] == 1
    assert _event(third, "candidate") is None
    (third_record,) = _records(third_state)
    assert third_record["candidate_id"] == second["candidate_id"]
    assert third_record["provenance"] == {
        "kind": "carried",
        "from_candidate_id": original["candidate_id"],
        "from_judge_sha256": original_judge,
        "match": "locus",
        "reason": mutation.CARRY_FORWARD_REASON,
    }
    assert mutation.record_provenance(third_record) == third_record["provenance"]


def test_f13_locus_collision_ignores_both_candidates_after_id_dedup(tmp_path: Path):
    source = "def flag():\n    return True\n"
    job = _job(source)
    first_prior = _job(source + "\nOTHER = 1\n")
    second_prior = _job(source + "\nOTHER = 2\n")
    first_record = _minimal_record(
        first_prior, judge="a" * 64, bucket="survived"
    )
    source_dir = tmp_path / "records"
    _write_records(
        source_dir,
        first_record,
        _minimal_record(second_prior, judge="b" * 64, bucket="survived"),
    )
    generation = source_dir / "judges" / first_record["judge_sha256"]
    generation.mkdir(parents=True)
    (generation / f"{first_record['candidate_id']}.json").write_text(
        json.dumps(first_record), encoding="utf-8"
    )

    index = mutation._carry_forward_source_index(source_dir)
    assert len(index.records) == 3

    matches, ignored = mutation._carry_forward_lookup(
        [job], (source_dir,), judge="c" * 64
    )

    assert matches == {}
    assert ignored == 1
    exact, exact_ignored = mutation._carry_forward_lookup(
        [first_prior], (source_dir,), judge="c" * 64
    )
    assert exact[mutation.candidate_id(first_prior)].kind == "exact-id"
    assert exact_ignored == 0


def test_carry_forward_identity_validation_rejects_malformed_records():
    job = _job("def flag():\n    return True\n")
    candidate = mutation.candidate_id(job)
    valid = _minimal_record(job, judge="a" * 64, bucket="survived")
    assert mutation._valid_carry_forward_identity(valid, candidate) is True

    no_locus = {k: v for k, v in valid.items() if k not in {"locus", "window_key"}}
    assert mutation._valid_carry_forward_identity(no_locus, candidate) is True

    def changed(**value):
        return {**valid, **value}

    malformed = [
        changed(schema_version=99),
        {k: v for k, v in valid.items() if k != "source_sha256"},
        changed(replacement_sha256="not-a-digest"),
        changed(locus=None),
        {k: v for k, v in valid.items() if k != "window_key"},
        changed(locus={**valid["locus"], "rel_end": valid["locus"]["rel_start"]}),
        changed(locus={**valid["locus"], "key": "0" * 64}),
        changed(path=7),
    ]

    for payload in malformed:
        assert mutation._valid_carry_forward_identity(payload, candidate) is False
    assert mutation._valid_carry_forward_identity(valid, "0" * 64) is False


def test_same_candidate_flat_and_generation_records_are_deduplicated(tmp_path: Path):
    job = _job("def flag():\n    return True\n")
    judge = "b" * 64
    record = _minimal_record(
        job,
        judge=judge,
        bucket="killed",
    )
    store = tmp_path / "records"
    _write_records(store, record)
    generation = store / "judges" / judge
    generation.mkdir(parents=True)
    (generation / f"{record['candidate_id']}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    (store / "not-a-candidate.json").write_text("{}", encoding="utf-8")

    paths = mutation._carry_forward_record_paths(store)
    index = mutation._carry_forward_source_index(store)
    matches, ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="a" * 64
    )
    assert len(paths) == 2
    assert len(index.records) == 2
    assert ignored == 0
    assert matches[mutation.candidate_id(job)].kind == "exact-id"
    same_judge, same_judge_ignored = mutation._carry_forward_lookup(
        [job], (store,), judge=judge
    )
    assert same_judge == {}
    assert same_judge_ignored == 0


def test_source_scan_skips_malformed_and_ineligible_records(tmp_path: Path, monkeypatch):
    job = _job("def flag():\n    return True\n")
    other_job = _job("def other():\n    return True\n", path="pkg/other.py")
    store = tmp_path / "records"
    store.mkdir()
    _write_records(
        store, _minimal_record(other_job, judge="a" * 64, bucket="crashed")
    )
    (store / f"{'a' * 64}.json").write_text("{", encoding="utf-8")
    (store / f"{'b' * 64}.json").write_text("[]", encoding="utf-8")
    (store / f"{'c' * 64}.json").write_text(
        json.dumps({"candidate_id": "invalid", "judge_sha256": "j" * 64,
                    "outcome_bucket": "killed"}),
        encoding="utf-8",
    )
    (store / f"{'d' * 64}.json").write_text(
        json.dumps(_minimal_record(job, judge="x" * 64, bucket="killed")),
        encoding="utf-8",
    )
    (store / f"{'e' * 64}.json").write_text(
        json.dumps(_minimal_record(job, judge="j" * 64, bucket="crashed")),
        encoding="utf-8",
    )
    (store / f"{'6' * 64}.json").write_text(
        json.dumps({**_minimal_record(job, judge="j" * 64, bucket="killed"),
                    "candidate_id": "7" * 64}),
        encoding="utf-8",
    )
    (store / f"{'8' * 64}.json").write_bytes(b"\xff")
    (store / f"{'4' * 64}.json").write_bytes(b"[" * 2048 + b"]" * 2048)
    oversized = store / f"{'f' * 64}.json"
    oversized.write_bytes(b" " * (mutation.MUTATION_STATE_RECORD_LIMIT + 1))
    symlink = store / f"{'9' * 64}.json"
    symlink.symlink_to(store / f"{'d' * 64}.json")
    (store / "judges").write_text("not a directory", encoding="utf-8")

    index = mutation._carry_forward_source_index(store)
    assert index.records == ()
    assert index.invalid_records >= 7
    assert mutation._carry_forward_record_paths(tmp_path / "absent") == ()

    def absent_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mutation.safeio, "read_bounded_input", absent_read)
    assert mutation._carry_forward_source_index(store).records == ()

    def rejected_read(*_args, **_kwargs):
        raise mutation.MutationStateError("unsafe source")

    monkeypatch.setattr(mutation.safeio, "read_bounded_input", rejected_read)
    assert mutation._carry_forward_source_index(store).records == ()


def test_unreadable_generation_keeps_other_carry_forward_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job("def flag():\n    return True\n")
    record = _minimal_record(job, judge="x" * 64, bucket="survived")
    store = tmp_path / "records"
    _write_records(store, record)
    blocked = store / mutation.JUDGE_GENERATIONS_DIR / ("a" * 64)
    other = store / mutation.JUDGE_GENERATIONS_DIR / ("b" * 64)
    for generation in (blocked, other):
        generation.mkdir(parents=True)
        (generation / f"{record['candidate_id']}.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

    real_scandir = mutation.os.scandir

    def guarded_scandir(path):
        if Path(path) == blocked:
            raise OSError("generation directory disappeared")
        return real_scandir(path)

    monkeypatch.setattr(mutation.os, "scandir", guarded_scandir)
    paths = mutation._carry_forward_record_paths(store)
    assert f"{blocked.relative_to(store)}/{record['candidate_id']}.json" not in paths
    assert f"{other.relative_to(store)}/{record['candidate_id']}.json" in paths
    assert f"{record['candidate_id']}.json" in paths


def test_carry_forward_lookup_honors_deadline_and_rejudge_outcomes(tmp_path: Path):
    job = _job("def flag():\n    return True\n")
    record = _minimal_record(
        job, judge="a" * 64, bucket="survived"
    )
    store = tmp_path / "records"
    _write_records(store, record)
    def deadline_check() -> float:
        return 1.0

    assert mutation._carry_forward_lookup(
        [job], (store,), judge="b" * 64,
        rejudge_outcomes=frozenset({"survived"}),
        deadline_check=deadline_check,
    )[0] == {}
    with pytest.raises(RuntimeError, match="expired"):
        mutation._carry_forward_lookup(
            [job], (store,), judge="b" * 64,
            deadline_check=lambda: (_ for _ in ()).throw(RuntimeError("expired")),
        )


def test_carry_forward_import_rejects_invalid_and_nonfinal_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job("def flag():\n    return True\n")
    source = tmp_path / "source"
    source.mkdir()
    flex = mutation.FlexOptions(carry_forward_sources=(source,))
    matched = mutation._CarryForwardMatch(
        kind="exact-id",
        source=source,
        payload={
            "candidate_id": mutation.candidate_id(job),
            "judge_sha256": "x" * 64,
            "outcome_bucket": "killed",
        },
        raw=b"{}",
        effective_judge="x" * 64,
    )
    monkeypatch.setattr(
        mutation,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: (
            {mutation.candidate_id(job): (matched,)},
            0,
            0,
            1,
        ),
    )
    monkeypatch.setattr(
        mutation,
        "_load_validated_state_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            mutation.MutationStateError("bad exact record")
        ),
    )
    bad_exact = mutation._carry_forward_records(
        flex,
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=b"baseline",
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert bad_exact.unmatched == 1

    monkeypatch.setattr(
        mutation,
        "_validate_carry_forward_match",
        lambda match, *_args, **_kwargs: match.payload,
    )
    matched = mutation._CarryForwardMatch(
        kind="window",
        source=source,
        payload={"candidate_id": mutation.candidate_id(job), "outcome_bucket": "survived"},
        effective_judge="x" * 64,
    )
    monkeypatch.setattr(
        mutation,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: (
            {mutation.candidate_id(job): (matched,)},
            0,
            0,
            1,
        ),
    )
    window_survivor = mutation._carry_forward_records(
        flex,
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert window_survivor.unmatched == 1

    nonfinal = mutation._CarryForwardMatch(
        kind="locus",
        source=source,
        payload={"candidate_id": mutation.candidate_id(job), "outcome_bucket": "crashed"},
        effective_judge="x" * 64,
    )
    monkeypatch.setattr(
        mutation,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: (
            {mutation.candidate_id(job): (nonfinal,)},
            0,
            0,
            1,
        ),
    )
    refused_bucket = mutation._carry_forward_records(
        flex,
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert refused_bucket.unmatched == 1


def test_carry_forward_skips_resumed_jobs_with_or_without_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    unmatched_job = _job("def unmatched():\n    return True\n")
    invalid_job = _job("def invalid():\n    return True\n")
    valid_job = _job("def valid():\n    return True\n")
    source = tmp_path / "source"
    _write_records(
        source,
        _minimal_record(invalid_job, judge="a" * 64, bucket="survived"),
        _minimal_record(valid_job, judge="a" * 64, bucket="survived"),
    )

    def validate(match, *_args, **_kwargs):
        if match.payload["candidate_id"] == mutation.candidate_id(invalid_job):
            return None
        return match.payload

    monkeypatch.setattr(
        mutation,
        "_validate_carry_forward_match",
        validate,
    )

    result = mutation._carry_forward_records(
        mutation.FlexOptions(carry_forward_sources=(source,)),
        jobs=[unmatched_job, invalid_job, valid_job],
        skip_candidate_ids=frozenset(
            {
                mutation.candidate_id(unmatched_job),
                mutation.candidate_id(invalid_job),
                mutation.candidate_id(valid_job),
            }
        ),
        judge="b" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert result.considered_total == 0
    assert result.unmatched == 0
    assert result.invalid_records == 1
    assert result.records == ()


@pytest.mark.parametrize("kind", ["locus", "window"])
def test_locus_and_window_kills_are_both_left_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
):
    job = _job("def flag():\n    return True\n")
    source = tmp_path / "source"
    source.mkdir()
    match = mutation._CarryForwardMatch(
        kind=kind,
        source=source,
        payload={"candidate_id": "a" * 64, "outcome_bucket": "killed"},
        effective_judge="x" * 64,
    )
    monkeypatch.setattr(
        mutation,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: ({mutation.candidate_id(job): (match,)}, 0, 0, 0),
    )
    monkeypatch.setattr(
        mutation,
        "_validate_carry_forward_match",
        lambda *_args, **_kwargs: match.payload,
    )
    result = mutation._carry_forward_records(
        mutation.FlexOptions(carry_forward_sources=(source,)),
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert result.records == ()
    assert result.kills_requeued == 1
    assert getattr(result, kind) == 1


def test_carry_forward_revalidates_before_publishing(tmp_path: Path, monkeypatch):
    job = _job("def flag():\n    return True\n")
    source = tmp_path / "source"
    source.mkdir()
    flex = mutation.FlexOptions(carry_forward_sources=(source,))
    chosen = {
        "candidate_id": mutation.candidate_id(job),
        "outcome_bucket": "survived",
        "judge_sha256": "x" * 64,
    }
    match = mutation._CarryForwardMatch(
        kind="exact-id",
        source=source,
        payload=chosen,
        raw=b"{}",
        effective_judge="x" * 64,
    )
    monkeypatch.setattr(
        mutation,
        "_carry_forward_match_candidates",
        lambda *_args, **_kwargs: ({mutation.candidate_id(job): (match,)}, 0, 0, 0),
    )
    calls = 0

    def loader(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return chosen
        raise mutation.MutationStateError("revalidation failed")

    monkeypatch.setattr(mutation, "_load_validated_state_record", loader)
    result = mutation._carry_forward_records(
        flex,
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=lambda: None,
        campaign_deadline_sha256=None,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert result.unmatched == 1
    assert result.records == ()


def test_carried_record_keeps_the_campaign_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job("def flag():\n    return True\n")
    source = tmp_path / "source"
    record = _minimal_record(job, judge="a" * 64, bucket="survived")
    _write_records(source, record)
    deadline = "d" * 64
    written = []

    monkeypatch.setattr(
        mutation, "_validate_carry_forward_match", lambda *_args, **_kwargs: record
    )
    monkeypatch.setattr(
        mutation,
        "_load_validated_state_record",
        lambda _root, _job, *, raw_override, **_kwargs: json.loads(raw_override),
    )
    monkeypatch.setattr(
        mutation,
        "_write_mutation_state_record",
        lambda _root, payload, **_kwargs: written.append(payload),
    )
    result = mutation._carry_forward_records(
        mutation.FlexOptions(carry_forward_sources=(source,)),
        jobs=[job],
        judge="j" * 64,
        state_root=tmp_path / "target",
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=deadline,
        cold_witness=False,
        equivalence_artifact=None,
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert result.survivors_carried == 1
    assert written[0]["campaign_deadline_sha256"] == deadline


def test_carry_forward_source_paths_are_deduplicated_and_require_existing_dirs(
    git_repo: GitRepo, tmp_path: Path
):
    inside = git_repo.path / ".assay" / "source"
    inside.mkdir(parents=True)
    assert _resolve_carry_forward_sources((inside, inside), git_repo.path) == (
        inside.resolve(),
    )
    with pytest.raises(LaneConfigError, match="not an existing directory"):
        _resolve_carry_forward_sources((tmp_path / "missing",), git_repo.path)
    outside = tmp_path / "outside-source"
    outside.mkdir()
    link = git_repo.path / "linked-source"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(LaneConfigError, match="inside the checkout"):
        _resolve_carry_forward_sources((link,), git_repo.path)


def test_window_lookup_is_kill_only_across_judges(tmp_path: Path):
    source = (
        "def flag():\n"
        "    marker = 1\n"
        "    left_a = 0\n"
        "    left_b = 0\n"
        "    left_c = 0\n"
        "    return True\n"
    )
    job = _job(source)
    current_fields = mutation._locus_fields_for_job(job)
    prior_kill = _job(source.replace("marker = 1", "marker = 2"))
    prior_survivor = _job(source.replace("marker = 1", "marker = 3"))
    kill_fields = mutation._locus_fields_for_job(prior_kill)
    survivor_fields = mutation._locus_fields_for_job(prior_survivor)
    assert kill_fields["window_key"] == current_fields["window_key"]
    assert kill_fields["locus"]["key"] != current_fields["locus"]["key"]
    assert survivor_fields["window_key"] == current_fields["window_key"]
    survivor = _minimal_record(prior_survivor, judge="a" * 64, bucket="survived")
    store = tmp_path / "prior"
    _write_records(store, survivor)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64
    )
    assert matches == {}
    kill = _minimal_record(prior_kill, judge="b" * 64, bucket="killed")
    _write_records(store, kill)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64
    )
    assert matches[mutation.candidate_id(job)].kind == "window"
    assert matches[mutation.candidate_id(job)].payload["outcome_bucket"] == "killed"


def test_duplicate_method_segments_have_no_locus_and_keep_exact_id_lookup(
    tmp_path: Path,
):
    source = (
        "class Flags:\n"
        "    def value(self):\n        return True\n"
        "    def value(self):\n        return True\n"
    )
    job = _job(source)
    assert mutation._locus_fields_for_job(job) == {}
    edited = _job(source + "\nOTHER = 1\n")
    store = tmp_path / "exact"
    _write_records(
        store,
        _minimal_record(edited, judge="a" * 64, bucket="killed"),
    )
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="b" * 64
    )
    assert matches == {}

    exact = _minimal_record(
        job,
        judge="a" * 64,
        bucket="killed",
    )
    _write_records(store, exact)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="b" * 64
    )
    assert matches[mutation.candidate_id(job)].kind == "exact-id"


@pytest.mark.parametrize(
    "source",
    ["FLAG = True\n", "class Flags:\n    enabled = True\n"],
)
def test_module_and_class_body_candidates_have_no_subfile_lookup(
    source: str, tmp_path: Path
):
    job = _job(source)
    assert mutation._locus_fields_for_job(job) == {}
    prior = _job(source + "\nOTHER = 1\n")
    store = tmp_path / "unused"
    match = _minimal_record(
        prior,
        judge="a" * 64,
        bucket="killed",
    )
    _write_records(store, match)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="b" * 64
    )
    assert matches == {}


@pytest.mark.parametrize(
    ("current", "edited", "base"),
    [
        ("FLAG = True\n", "FLAG = True\nOTHER = 1\n", "FLAG = False\n"),
        (
            "class Flags:\n    enabled = True\n",
            "class Flags:\n    enabled = True\nOTHER = 1\n",
            "class Flags:\n    enabled = False\n",
        ),
    ],
)
def test_f11_module_and_class_body_edits_run_the_full_judge(
    git_repo: GitRepo, tmp_path: Path, current: str, edited: str, base: str
):
    _seed_source(git_repo, current, base)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    assert _event(first, "candidate")["outcome_bucket"] == "survived"

    git_repo.write("pkg/flags.py", edited)
    git_repo.commit_all("edit outside the non-locus site")
    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
    )
    assert code == 1
    carry = _event(final, "carry_forward")
    assert (carry["exact_id"], carry["locus"], carry["window"]) == (0, 0, 0)
    assert carry["unmatched"] == 1
    assert _event(final, "candidate")["outcome_bucket"] == "survived"
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert "provenance" not in record
    assert "carry_over" not in record


@pytest.mark.parametrize("value", [[], ("not-a-path",)])
def test_flex_options_rejects_invalid_carry_forward_sources(value):
    with pytest.raises(ValueError, match="carry_forward_sources must be a tuple of paths"):
        mutation.FlexOptions(carry_forward_sources=value)


def test_flex_options_marks_carry_forward_as_another_store(tmp_path: Path):
    flex = mutation.FlexOptions(carry_forward_sources=(tmp_path,))
    assert flex.reads_other_stores is True
    assert flex.reads_carry_over_stores is False


def test_list_pending_adds_classification_and_summary_only_with_the_flag(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, _events = _run(git_repo, source_store, tmp_path / "first.jsonl")
    git_repo.write("pkg/other.py", "VALUE = 1\n")
    git_repo.commit_all("change the judged tree outside the candidate")
    _code, current_events = _run(
        git_repo, tmp_path / "current-state", tmp_path / "current.jsonl"
    )
    judge = _event(current_events, "candidates")["judge_sha256"]
    empty_state = tmp_path / "empty-state"
    empty_state.mkdir()
    common = (
        "--list-pending",
        "--state-dir",
        str(empty_state),
        "--judge",
        judge,
    )
    plain = _plan(git_repo, *common)
    flagged = _plan(
        git_repo,
        *common,
        "--carry-forward",
        str(source_store),
    )
    assert plain[0] == flagged[0] == 0
    assert plain[1].strip().split()[0] == flagged[1].split()[0]
    assert "carry_forward=exact-id" in flagged[1]
    assert "carry_forward=" not in plain[1]
    assert "carry_forward exact-id=1" in flagged[2]
    assert plain[2] == _plan(git_repo, *common)[2]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--carry-forward", "{inside}"), "require --resume"),
        (("--carry-forward", "{inside}", "--carry-over", "{inside}",
          "--carry-over-reason", "why"), "cannot be combined with --carry-over"),
        (("--carry-forward", "{inside}", "--carry-over-reason", "why"),
         "cannot be combined with --carry-over-reason"),
    ],
)
def test_run_refuses_carry_forward_conflicts(
    git_repo: GitRepo,
    tmp_path: Path,
    extra: tuple[str, ...],
    message: str,
):
    _seed_method(git_repo, _BLIND)
    inside = git_repo.path / ".assay" / "prior"
    inside.mkdir(parents=True)
    replacements = {
        "{inside}": str(inside),
    }
    argv = tuple(replacements.get(value, value) for value in extra)
    state = tmp_path / "state"
    progress = tmp_path / "progress.jsonl"
    if "require --resume" in message:
        # This case deliberately omits the ordinary resume switch.
        err = io.StringIO()
        code = main(
            ["run", "unit", "--file", str(git_repo.path / "assay.toml"),
             "--state-dir", str(state), "--progress", str(progress), *argv],
            stdout=io.StringIO(), stderr=err,
        )
    else:
        code, error = _invoke_run(git_repo, state, progress, *argv)
        err = io.StringIO(error)
    assert code == 2
    assert message in err.getvalue()


def test_carry_forward_requires_native_r2_and_list_pending_context(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    inside = git_repo.path / ".assay" / "prior"
    inside.mkdir(parents=True)

    missing_judge = _plan(
        git_repo,
        "--list-pending",
        "--state-dir",
        str(tmp_path / "empty"),
        "--carry-forward",
        str(inside),
    )
    assert missing_judge[0] == 2
    assert "requires --judge" in missing_judge[2]

    no_listing = _plan(git_repo, "--carry-forward", str(inside))
    assert no_listing[0] == 2
    assert "apply only to --list-pending" in no_listing[2]

    non_r2 = _LANE.replace('rigor = ["R0", "R2"]', 'rigor = ["R0"]')
    non_r2 = non_r2.split("[lanes.unit.judge]", 1)[0]
    git_repo.write("assay.toml", non_r2)
    git_repo.commit_all("remove R2")
    code, error = _invoke_run(
        git_repo,
        tmp_path / "state",
        tmp_path / "progress.jsonl",
        "--carry-forward",
        str(inside),
    )
    assert code == 2
    assert "requires a native R2 mutation lane" in error


def test_carry_forward_source_must_be_inside_the_checkout(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    outside = tmp_path / "outside"
    outside.mkdir()
    code, error = _invoke_run(
        git_repo,
        tmp_path / "state",
        tmp_path / "progress.jsonl",
        "--carry-forward",
        str(outside),
    )
    assert code == 2
    assert "must resolve inside the checkout" in error


def test_carry_forward_finds_same_candidate_id_from_an_earlier_judge(tmp_path: Path):
    job = _job("def flag():\n    return True\n")
    foreign = _minimal_record(
        job,
        candidate=mutation.candidate_id(job),
        judge="a" * 64,
        bucket="survived",
    )
    store = tmp_path / "foreign"
    _write_records(store, foreign)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="b" * 64
    )
    assert matches[mutation.candidate_id(job)].kind == "exact-id"


def test_prior_kill_wins_when_survivor_and_kill_claim_the_candidate(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    survivor_store = git_repo.path / ".assay" / "prior-survivor"
    _code, survivor_events = _run(
        git_repo, survivor_store, tmp_path / "survivor.jsonl"
    )
    survivor_candidate = _event(survivor_events, "candidate")
    assert survivor_candidate["outcome_bucket"] == "survived"

    git_repo.write("tests/judge.sh", _KILL_METHOD)
    git_repo.commit_all("change the prior judge to kill the candidate")
    kill_store = git_repo.path / ".assay" / "prior-kill"
    _code, kill_events = _run(git_repo, kill_store, tmp_path / "kill.jsonl")
    kill_candidate = _event(kill_events, "candidate")
    assert kill_candidate["outcome_bucket"] == "killed"
    assert kill_candidate["candidate_id"] == survivor_candidate["candidate_id"]

    prior_judges = {
        _event(survivor_events, "candidates")["judge_sha256"],
        _event(kill_events, "candidates")["judge_sha256"],
    }
    assert len(prior_judges) == 2
    git_repo.write("pkg/other.py", "VALUE = 1\n")
    git_repo.commit_all("change the judged tree again")

    carry_store = git_repo.path / ".assay" / "prior-combined"
    candidate_id = survivor_candidate["candidate_id"]
    for store, events in ((survivor_store, survivor_events), (kill_store, kill_events)):
        source_judge = _event(events, "candidates")["judge_sha256"]
        destination = carry_store / "judges" / source_judge
        destination.mkdir(parents=True, exist_ok=True)
        prior_record = next(store.glob("*.json"))
        (destination / f"{candidate_id}.json").write_bytes(prior_record.read_bytes())

    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(carry_store),
    )
    carry = _event(final, "carry_forward")
    assert carry["from_judges"] == 2
    assert carry["kills_requeued"] == 1
    assert carry["survivors_carried"] == 0
    assert _event(final, "candidate")["outcome_bucket"] == "killed"
    assert code == 0


def test_stale_locus_record_is_skipped_and_counted(git_repo: GitRepo, tmp_path: Path):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, _first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    prior_path = next(source_store.glob("*.json"))
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    prior["locus"]["key"] = "0" * 64
    prior_path.write_text(json.dumps(prior), encoding="utf-8")

    git_repo.write("tests/judge.sh", _KILL_METHOD)
    git_repo.commit_all("change the judge while preserving the candidate")
    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
    )
    carry = _event(final, "carry_forward")
    assert carry["invalid_records"] == 1
    assert _event(final, "candidate")["outcome_bucket"] == "killed"
    assert code == 0


def test_structural_but_invalid_survivor_is_rejected_then_rerun(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, _first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    prior_path = next(source_store.glob("*.json"))
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    prior["terminal_result"]["returncode"] = "not-an-integer"
    prior_path.write_text(json.dumps(prior), encoding="utf-8")

    git_repo.write("tests/judge.sh", _BLIND + "# whole-tree judge change\n")
    git_repo.commit_all("change the judge")
    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
        tmp_path / "final.jsonl",
        "--carry-forward",
        str(source_store),
    )
    assert code == 1
    carry = _event(final, "carry_forward")
    assert carry["invalid_records"] == 1
    assert carry["survivors_carried"] == 0
    assert _event(final, "candidate")["outcome_bucket"] == "survived"
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert "provenance" not in record
    assert "carry_over" not in record


def test_f17_carry_forward_shards_merge_with_provenance_intact(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_source(
        git_repo,
        "a = True\nb = True\nc = True\nd = True\n",
        "a = False\nb = False\nc = False\nd = False\n",
    )
    source_store = git_repo.path / ".assay" / "prior"
    _code, _first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    prior_records = _records(source_store)
    assert {record["outcome_bucket"] for record in prior_records} == {"survived"}
    assert len(mutation._carry_forward_record_paths(source_store)) == 4
    source_ids = tuple(record["candidate_id"] for record in prior_records)

    git_repo.write("tests/judge.sh", _BLIND + "# whole-tree judge change\n")
    git_repo.commit_all("judge change")
    shard_count = 3
    shards = [tmp_path / f"shard-{index}" for index in range(shard_count)]
    verdicts = [tmp_path / f"shard-{index}.json" for index in range(shard_count)]
    expected_by_shard = [
        {
            source_ids[position]
            for position in mutation.select_mutation_shard(
                source_ids, index=index, count=shard_count
            )
        }
        for index in range(shard_count)
    ]
    assert all(expected_by_shard[:2])
    assert not expected_by_shard[2]

    for index, (store, verdict, expected_ids) in enumerate(
        zip(shards, verdicts, expected_by_shard, strict=True)
    ):
        code, events = _run(
            git_repo,
            store,
            tmp_path / f"shard-{index}.jsonl",
            "--shard",
            f"{index}/{shard_count}",
            "--carry-forward",
            str(source_store),
            verdict=verdict,
        )
        assert code == (1 if expected_ids else 5)
        assert _event(events, "carry_forward")["survivors_carried"] == len(expected_ids)
        assert _event(events, "candidate") is None
        shard_records = _records(store)
        assert {record["candidate_id"] for record in shard_records} == expected_ids
        assert {record["provenance"]["kind"] for record in shard_records} <= {
            "carried"
        }
        claim = next(
            item
            for item in json.loads(verdict.read_text(encoding="utf-8"))["claims"]
            if item["rigor"] == "R2"
        )
        assert claim["status"] == ("FAIL" if expected_ids else "INCONCLUSIVE")
        if not expected_ids:
            assert claim["reason_code"] == "NO_MUTANTS"
        assert main(
            ["verify", str(verdict)], stdout=io.StringIO(), stderr=io.StringIO()
        ) == 0

    merged = tmp_path / "merged"
    merged_verdict = tmp_path / "merged.json"
    merge_args = [
        arg
        for store in shards
        for arg in ("--merge-shard-store", str(store))
    ]
    code, events = _run(
        git_repo,
        merged,
        tmp_path / "merged.jsonl",
        *merge_args,
        "--carry-forward",
        str(source_store),
        verdict=merged_verdict,
    )
    assert code == 1
    assert _event(events, "carry_over")["copied_total"] == 4
    carry = _event(events, "carry_forward")
    assert (carry["exact_id"], carry["locus"], carry["window"]) == (0, 0, 0)
    assert carry["same_judge_resume"] == 0
    records = _records(merged)
    assert len(records) == 4
    assert {record["provenance"]["kind"] for record in records} == {"carried"}
    assert main(
        ["verify", str(merged_verdict)], stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0
    r2 = next(
        claim
        for claim in json.loads(merged_verdict.read_text(encoding="utf-8"))["claims"]
        if claim["rigor"] == "R2"
    )
    assert len(r2["mutation"]["survived"]) == 4


def test_carry_forward_composes_with_reuse_from(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    prior_verdict = tmp_path / "prior-verdict.json"
    _code, _first = _run(
        git_repo, source_store, tmp_path / "first.jsonl", verdict=prior_verdict
    )
    git_repo.write("tests/judge.sh", _BLIND + "# whole-tree judge change\n")
    git_repo.commit_all("judge change")
    _code, events = _run(
        git_repo,
        tmp_path / "final",
        tmp_path / "final.jsonl",
        "--reuse-from",
        str(prior_verdict),
        "--carry-forward",
        str(source_store),
    )
    assert _event(events, "carry_forward")["survivors_carried"] == 1


def test_carry_forward_composes_with_the_candidate_file_pilot(
    git_repo: GitRepo, tmp_path: Path
):
    _seed_method(git_repo, _BLIND)
    source_store = git_repo.path / ".assay" / "prior"
    _code, first = _run(git_repo, source_store, tmp_path / "first.jsonl")
    candidate = _event(first, "candidate")["candidate_id"]
    listing = tmp_path / "candidates.txt"
    listing.write_text(candidate + "\n", encoding="utf-8")

    git_repo.write("tests/judge.sh", _BLIND + "# whole-tree judge change\n")
    git_repo.commit_all("judge change")
    code, events = _run(
        git_repo,
        tmp_path / "pilot-state",
        tmp_path / "pilot.jsonl",
        "--candidates-file",
        str(listing),
        "--carry-forward",
        str(source_store),
    )
    assert code == 6
    assert _event(events, "carry_forward")["survivors_carried"] == 1


def test_carried_survivor_count_ignores_unusable_and_noncarried_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = tmp_path / "store"
    store.mkdir()
    source = tmp_path / "source"
    source.mkdir()

    def marked(name: str, kind: str, bucket: str = "survived") -> dict:
        record = _minimal_record(
            _job(f"def {name}():\n    return True\n"),
            judge="a" * 64,
            bucket=bucket,
        )
        mutation._set_carry_over_provenance(
            record, kind=kind, match="exact-id",
            from_candidate_id=record["candidate_id"],
            from_judge_sha256="a" * 64, into_judge_sha256="d" * 64,
            source=source,
            reason="carry-forward" if kind == "carried" else "operator",
            imported_at="2026-11-24T00:00:00Z",
        )
        return record

    valid = marked("valid", "carried")
    _write_records(
        store,
        valid,
        _minimal_record(
            _job("def fresh():\n    return True\n"),
            judge="a" * 64,
            bucket="survived",
        ),
    )
    _write_records(store, marked("imported", "imported"))
    _write_records(store, marked("killed", "carried", "killed"))
    invalid = _minimal_record(
        _job("def invalid():\n    return True\n"),
        judge="a" * 64,
        bucket="survived",
    )
    invalid["provenance"] = {
        "kind": "carried",
        "from_candidate_id": invalid["candidate_id"],
        "from_judge_sha256": "a" * 64,
        "match": "exact-id",
    }
    _write_records(store, invalid)
    (store / f"{'1' * 64}.json").write_text("{", encoding="utf-8")
    (store / f"{'2' * 64}.json").write_text("[]", encoding="utf-8")
    (store / f"{'3' * 64}.json").mkdir()
    (store / "not-a-candidate.json").write_text("{}", encoding="utf-8")
    symlink = store / f"{'4' * 64}.json"
    symlink.symlink_to(store / f"{valid['candidate_id']}.json")

    assert mutation.carried_survivor_count(store) == 1
    assert mutation.carried_survivor_count(tmp_path / "absent") == 0

    monkeypatch.setattr(
        mutation.safeio, "read_bounded_input", lambda *_args, **_kwargs: None
    )
    assert mutation.carried_survivor_count(store) == 0

    def rejected_read(*_args, **_kwargs):
        raise LaneConfigError("unreadable")

    monkeypatch.setattr(mutation.safeio, "read_bounded_input", rejected_read)
    assert mutation.carried_survivor_count(store) == 0


def test_equal_matches_choose_lexicographically_smallest_judge_independent_of_write_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job("def flag():\n    return True\n")
    candidate = mutation.candidate_id(job)
    records = [
        _minimal_record(job, judge="b" * 64, bucket="survived"),
        _minimal_record(job, judge="a" * 64, bucket="survived"),
    ]
    stores = (tmp_path / "first", tmp_path / "second")
    for store, ordered in ((stores[0], records), (stores[1], list(reversed(records)))):
        for record in ordered:
            generation = store / "judges" / record["judge_sha256"]
            generation.mkdir(parents=True, exist_ok=True)
            (generation / f"{candidate}.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
    winners = []
    for store in stores:
        imported = []

        def load_record(_root, _job, *, raw_override, **_kwargs):
            return json.loads(raw_override)

        def capture_record(_root, record, *, imported=imported, **_kwargs):
            imported.append(record)

        monkeypatch.setattr(mutation, "_load_validated_state_record", load_record)
        monkeypatch.setattr(mutation, "_write_mutation_state_record", capture_record)
        result = mutation._carry_forward_records(
            mutation.FlexOptions(carry_forward_sources=(store,)),
            jobs=[job],
            judge="c" * 64,
            state_root=tmp_path / "target",
            state_root_fd=None,
            state_root_guard=None,
            campaign_deadline_sha256=None,
            cold_witness=False,
            equivalence_artifact=None,
            baseline_equivalence=None,
            kill_signal_artifact=None,
            rejudge_outcomes=frozenset(),
            clock=lambda: datetime.now(timezone.utc),
        )
        assert result.survivors_carried == 1
        assert len(imported) == 1
        winners.append(imported[0]["provenance"]["from_judge_sha256"])
    assert winners == ["a" * 64, "a" * 64]
