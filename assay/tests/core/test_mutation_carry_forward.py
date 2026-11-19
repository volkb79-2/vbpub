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
    git_repo.write("pkg/other.py", "VALUE = 1\n")
    git_repo.commit_all("change the judged tree outside the candidate")

    verdict_path = tmp_path / "carried-verdict.json"
    final_state = tmp_path / "final-state"
    code, final = _run(
        git_repo,
        final_state,
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
    record = json.loads(next(final_state.glob("*.json")).read_text(encoding="utf-8"))
    assert record["judge_sha256"] == judge
    assert record["carry_over"]["from_judge_sha256"] == judge
    assert record["carry_over"]["into_judge_sha256"] != judge
    assert record["outcome_bucket"] == "survived"
    assert record["carry_over"]["schema_version"] == 2
    assert record["carry_over"]["reason"] == "carry-forward"
    assert record["carry_over"]["locus_match"] == "exact-id"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    r2 = next(claim for claim in verdict["claims"] if claim["rigor"] == "R2")
    assert r2["status"] == "FAIL"
    assert code != 0


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
    assert record["carry_over"]["from_judge_sha256"] != record["carry_over"]["into_judge_sha256"]
    assert record["carry_over"]["locus_match"] == "locus"
    assert record["outcome_bucket"] == "survived"
    r2 = next(
        claim
        for claim in json.loads(verdict_path.read_text(encoding="utf-8"))["claims"]
        if claim["rigor"] == "R2"
    )
    assert r2["status"] == "FAIL"
    assert code != 0


def test_ambiguous_locus_records_are_both_ignored(git_repo: GitRepo, tmp_path: Path):
    source = "def flag():\n    return True\n"
    job = _job(source)
    first_prior = _job(source + "\nOTHER = 1\n")
    second_prior = _job(source + "\nOTHER = 2\n")
    records = (
        _minimal_record(first_prior, judge="a" * 64, bucket="survived"),
        _minimal_record(second_prior, judge="b" * 64, bucket="survived"),
    )
    source_dir = tmp_path / "records"
    _write_records(source_dir, *records)

    matches, ignored = mutation._carry_forward_lookup(
        [job], (source_dir,), judge="c" * 64
    )

    assert matches == {}
    assert ignored == 1


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
    index = mutation._carry_forward_source_index(
        store, judge=judge, rejudge_outcomes=frozenset()
    )
    matches, ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="a" * 64
    )
    assert len(paths) == 2
    assert index.loci.ambiguous_loci() == frozenset()
    assert ignored == 0
    assert matches[mutation.candidate_id(job)].kind == "exact-id"


def test_source_scan_skips_malformed_and_ineligible_records(tmp_path: Path, monkeypatch):
    job = _job("def flag():\n    return True\n")
    store = tmp_path / "records"
    store.mkdir()
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

    index = mutation._carry_forward_source_index(
        store, judge="j" * 64, rejudge_outcomes=frozenset()
    )
    assert index.exact == {}
    assert index.invalid_records >= 7
    assert index.loci.ambiguous_loci() == frozenset()
    assert mutation._carry_forward_record_paths(tmp_path / "absent") == ()

    def absent_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mutation.safeio, "read_bounded_input", absent_read)
    assert mutation._carry_forward_source_index(
        store, judge="j" * 64, rejudge_outcomes=frozenset()
    ).exact == {}

    def rejected_read(*_args, **_kwargs):
        raise mutation.MutationStateError("unsafe source")

    monkeypatch.setattr(mutation.safeio, "read_bounded_input", rejected_read)
    assert mutation._carry_forward_source_index(
        store, judge="j" * 64, rejudge_outcomes=frozenset()
    ).exact == {}


def test_carry_forward_lookup_honors_deadline_and_rejudge_outcomes(tmp_path: Path):
    job = _job("def flag():\n    return True\n")
    record = _minimal_record(
        job, judge="x" * 64, bucket="survived"
    )
    store = tmp_path / "records"
    _write_records(store, record)
    calls = 0

    def deadline_check() -> float:
        nonlocal calls
        calls += 1
        return 1.0

    assert mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64,
        rejudge_outcomes=frozenset({"survived"}),
        deadline_check=deadline_check,
    )[0] == {}
    assert calls >= 2
    with pytest.raises(RuntimeError, match="expired"):
        mutation._carry_forward_lookup(
            [job], (store,), judge="j" * 64,
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
        baseline_equivalence=None,
        kill_signal_artifact=None,
        rejudge_outcomes=frozenset(),
        clock=lambda: datetime.now(timezone.utc),
    )
    assert bad_exact.unmatched == 1

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
        lambda *_args, **_kwargs: ({mutation.candidate_id(job): (match,)}, 0, 0),
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
        lambda *_args, **_kwargs: ({mutation.candidate_id(job): (match,)}, 0, 0),
    )
    calls = 0

    def loader(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return chosen if calls == 1 else None

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
    assert calls == 2
    assert result.unmatched == 1
    assert result.records == ()


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
    exact = _minimal_record(
        job,
        judge="x" * 64,
        bucket="killed",
    )
    store = tmp_path / "exact"
    _write_records(store, exact)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64
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
        judge="x" * 64,
        bucket="killed",
    )
    _write_records(store, match)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64
    )
    assert matches == {}


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
        (("--carry-forward", "{inside}", "--shard", "0/1"), "cannot be combined with --shard"),
        (("--carry-forward", "{inside}", "--carry-over", "{inside}",
          "--carry-over-reason", "why"), "cannot be combined with --carry-over"),
        (("--carry-forward", "{inside}", "--carry-over-reason", "why"),
         "cannot be combined with --carry-over-reason"),
        (("--carry-forward", "{inside}", "--reuse-from", "{reuse}"),
         "cannot be combined with --reuse-from"),
        (("--carry-forward", "{inside}", "--candidates-file", "{file}"),
         "--carry-forward, --merge-state"),
        (("--carry-forward", "{inside}", "--merge-shard-store", "{inside}"),
         "cannot be combined with --shard"),
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
    candidate_file = tmp_path / "candidates.txt"
    candidate_file.write_text("", encoding="utf-8")
    replacements = {
        "{inside}": str(inside),
        "{reuse}": str(tmp_path / "unused-verdict.json"),
        "{file}": str(candidate_file),
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
        judge="x" * 64,
        bucket="survived",
    )
    store = tmp_path / "foreign"
    _write_records(store, foreign)
    matches, _ignored = mutation._carry_forward_lookup(
        [job], (store,), judge="j" * 64
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

        def capture_record(_root, record, **_kwargs):
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
        winners.append(imported[0]["carry_over"]["from_judge_sha256"])
    assert winners == ["a" * 64, "a" * 64]
