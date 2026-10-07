"""Adversarial boundary cases for mutation validation, state, and shards."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from concurrent.futures import Future as ConcurrentFuture
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import mutation
from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    prepared_snapshot,
    zero_resource_limit_evidence_dict,
)
from assay.adapters.python import PythonAdapter
from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationDiscoveryError, MutationStateError
from assay.verdict import Mutation


def _job():
    text = "x = True\n"
    site = mutation.MutationSite(
        start_byte=4,
        end_byte=8,
        replacement=b"False",
        lineno=1,
        operator="python:bool-const-flip",
        description="True->False",
    )
    return mutation.MutantJob(path="src/mod.py", original_text=text, site=site)


def _record(job=None):
    job = _job() if job is None else job
    source = job.original_text.encode()
    mutated = job.site.apply(source)
    return {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        "candidate_id": mutation.candidate_id(job),
        "path": job.path,
        "replacement_sha256": hashlib.sha256(job.site.replacement).hexdigest(),
        "operator": job.site.operator,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "outcome_bucket": "killed",
        "judge_sha256": "j" * 64,
        "execution": {"mode": "full"},
        "lineno": job.site.lineno,
        "start_byte": job.site.start_byte,
        "end_byte": job.site.end_byte,
        "description": job.site.description,
        "mutated_file_sha256": hashlib.sha256(mutated).hexdigest(),
        "resource_limit_evidence": zero_resource_limit_evidence_dict(),
    }


def _write_record(root: Path, payload):
    root.mkdir(parents=True, exist_ok=True)
    identity = payload.get("candidate_id", mutation.candidate_id(_job()))
    path = root / mutation.mutation_state_record_name(identity)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_site_validator_rejects_a_utf8_continuation_byte_boundary():
    target = mutation.MutationTarget(
        path="src/mod.py", text="π = 1\n", lines=frozenset({1})
    )
    site = mutation.MutationSite(
        start_byte=1,
        end_byte=2,
        replacement=b"x",
        lineno=1,
        operator="python:compare-swap",
        description="inside-pi",
    )
    with pytest.raises(MutationDiscoveryError, match="inside a UTF-8 character"):
        mutation._validate_sites(
            (site,), target=target, operators=(site.operator,), remaining=1
        )


def test_site_validator_refuses_a_splice_that_produces_invalid_utf8():
    target = mutation.MutationTarget(
        path="src/mod.py", text="x = 1\n", lines=frozenset({1})
    )
    site = SimpleNamespace(
        start_byte=0,
        end_byte=1,
        replacement=b"z",
        lineno=1,
        operator="python:compare-swap",
        description="bad-adapter-output",
        identity="site",
        apply=lambda _source: b"\xff",
    )
    with pytest.raises(MutationDiscoveryError, match="produces invalid UTF-8"):
        mutation._validate_sites(
            (site,), target=target, operators=(site.operator,), remaining=1
        )


class _FailingProgressStream:
    def __init__(self, *, write_error=False, close_error=False):
        self.write_error = write_error
        self.close_error = close_error

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        if self.close_error:
            raise OSError("late ENOSPC")
        return False

    def write(self, _text):
        if self.write_error:
            raise OSError("write failed")

    def flush(self):
        return None


@pytest.mark.parametrize(
    ("stream_options", "message"),
    [
        ({"write_error": True}, "cannot write to the progress destination"),
        ({"close_error": True}, "cannot close the progress destination"),
    ],
)
def test_progress_writer_types_write_and_close_failures(
    tmp_path, monkeypatch, stream_options, message
):
    destination = tmp_path / "progress.jsonl"
    stream = _FailingProgressStream(**stream_options)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: stream)

    with pytest.raises(AssayError, match=message):
        with mutation.progress_writer(destination) as write:
            if stream_options.get("write_error"):
                write({"event": "run"})


def test_progress_stream_exposes_the_monotonic_start_used_by_elapsed():
    samples = iter((4.25, 4.75))
    stream = mutation.ProgressStream(
        lambda _event: None,
        clock=lambda: "2026-09-26T00:00:00+00:00",
        monotonic=lambda: next(samples),
    )
    assert stream.started_monotonic == 4.25
    assert stream.elapsed() == 0.5


def test_progress_stream_refuses_an_event_outside_its_closed_vocabulary():
    stream = mutation.ProgressStream(
        lambda _event: None,
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        monotonic=lambda: 5.0,
    )
    with pytest.raises(ValueError, match="not in the closed progress vocabulary"):
        stream.emit({"event": "invented"})


def test_progress_stream_emits_its_run_header_only_once():
    events = []
    stream = mutation.ProgressStream(
        events.append,
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        monotonic=lambda: 5.0,
    )

    stream.emit_run_header(commit="a" * 40, lane="package")
    stream.emit_run_header(commit="b" * 40, lane="different")

    assert [event["event"] for event in events] == ["run"]
    assert events[0]["commit"] == "a" * 40


@pytest.mark.parametrize("candidate", [None, "", "g" * 64, "a" * 63])
def test_state_record_name_rejects_noncanonical_candidate_ids(candidate):
    with pytest.raises(ValueError, match="candidate id must be a 64-character"):
        mutation.mutation_state_record_name(candidate)


def test_atomic_state_record_write_tolerates_a_missing_temp_during_cleanup(
    tmp_path, monkeypatch
):
    job = _job()
    monkeypatch.setattr(
        mutation.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        mutation.os,
        "unlink",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError("already gone")),
    )
    with pytest.raises(OSError, match="replace failed"):
        mutation._write_mutation_state_record(
            tmp_path, {"candidate_id": mutation.candidate_id(job)}
        )


@pytest.mark.parametrize(
    ("remove", "message"),
    [
        ("candidate_id", "missing candidate_id"),
        ("outcome_bucket", "missing outcome_bucket"),
    ],
)
def test_state_record_refuses_missing_identity_and_outcome_fields(
    tmp_path, remove, message
):
    job = _job()
    payload = _record(job)
    payload.pop(remove)
    _write_record(tmp_path, payload)
    with pytest.raises(MutationStateError, match=message):
        mutation._load_validated_state_record(tmp_path, job, judge="j" * 64)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("outcome_bucket", "invented", "unknown outcome bucket"),
        ("execution", [], "invalid execution provenance"),
    ],
)
def test_state_record_refuses_unknown_buckets_and_execution_shapes(
    tmp_path, field, value, message
):
    job = _job()
    payload = _record(job)
    payload[field] = value
    _write_record(tmp_path, payload)
    with pytest.raises(MutationStateError, match=message):
        mutation._load_validated_state_record(tmp_path, job, judge="j" * 64)


@pytest.mark.parametrize(
    ("execution", "message"),
    [
        ([], "execution must be an object"),
        ({"mode": "mystery"}, "execution mode is unknown"),
        ({"mode": "full", "extra": True}, "execution contains unknown fields"),
        ({"mode": "full", "witness": {"node_id": "x"}}, "invalid shape"),
    ],
)
def test_execution_state_decoder_rejects_unknown_or_malformed_shapes(
    execution, message
):
    with pytest.raises((TypeError, ValueError), match=message):
        mutation._execution_from_state_record({"execution": execution})


def test_execution_state_decoder_accepts_a_witness_prefix_receipt():
    node = "tests/test_mod.py::test_behavior"
    decoded = mutation._execution_from_state_record(
        {
            "execution": {
                "mode": "witness-prefix",
                "witness": {
                    "node_id": node,
                    "when": "call",
                    "outcome": "failed",
                    "session_exit_status": 1,
                    "process_exit_status": 1,
                },
                "prior_verdict_sha256": "a" * 64,
                "prior_node_id": node,
                "current_node_id": node,
            }
        }
    )
    assert decoded.mode == "witness-prefix"
    assert decoded.witness.node_id == node


def test_resumed_outcome_decoder_types_malformed_records():
    with pytest.raises(MutationStateError, match="invalid resumed mutation record"):
        mutation._outcome_from_record({})


def test_resumed_mutation_merge_refuses_a_duplicate_candidate():
    job = _job()
    payload = _record(job)
    outcome = mutation._outcome_from_record(payload)
    current = Mutation(candidate_count=1, total=1, killed=(outcome,))

    with pytest.raises(MutationStateError, match="repeat 1 candidate identity"):
        mutation.merge_mutations(current, [payload])


def test_mutation_score_is_zero_when_no_mutant_was_attempted():
    assert mutation.mutation_pct(Mutation(candidate_count=0, total=0)) == 0.0


def _run_mutation_args(**overrides):
    values = {
        "baseline": SimpleNamespace(
            outcome=Outcome.PASS,
            started="2026-09-26T00:00:00+00:00",
            ended="2026-09-26T00:00:01+00:00",
        ),
        "prepared": None,
        "plan": None,
        "deadline": None,
        "targets": (),
        "adapter": None,
        "jobs": 1,
        "max_mutants": 10,
        "operators": ("python:compare-swap",),
        "process_runner": lambda *_args, **_kwargs: None,
        "clock": lambda: "2026-09-26T00:00:00+00:00",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_mutants": True}, "max_mutants must be an integer"),
        ({"max_mutants": "10"}, "max_mutants must be an integer"),
        ({"max_mutants": 0}, "max_mutants must be in 1..10,000"),
        ({"max_mutants": 10_001}, "max_mutants must be in 1..10,000"),
        ({"budget_per_candidate_seconds": True}, "positive finite number"),
        ({"budget_per_candidate_seconds": "fast"}, "positive finite number"),
        ({"budget_per_candidate_seconds": math.nan}, "positive finite number"),
        ({"budget_per_candidate_seconds": 0}, "positive finite number"),
        ({"resume": 1}, "resume must be a boolean"),
        ({"resume": True}, "resume requires the caller's authoritative state_root"),
        (
            {"shard_index": 0, "state_root": Path("/tmp/state")},
            "requires both shard_index and shard_count",
        ),
    ],
)
def test_run_mutation_validates_candidate_bounds_and_resume_inputs(
    overrides, message
):
    with pytest.raises(ValueError, match=message):
        mutation.run_mutation(**_run_mutation_args(**overrides))


@pytest.mark.parametrize("count", [0, mutation.MAX_SHARD_COUNT + 1])
def test_mutation_shards_refuse_counts_outside_the_declared_range(count):
    with pytest.raises(ValueError, match="shard count must be in"):
        mutation.select_mutation_shard((), index=0, count=count)


@pytest.mark.parametrize("key", ["/outside/mod.py", "../outside/mod.py"])
def test_report_paths_refuse_absolute_and_parent_relative_keys(tmp_path, key):
    report = SimpleNamespace(sources={key: object()})
    with pytest.raises(AssayError, match="keys must be relative") as caught:
        mutation._resolve_report_paths(
            report,
            run_cwd=tmp_path / "repo" / "app",
            repo_top=tmp_path / "repo",
            source_root_paths=(tmp_path / "repo" / "app" / "src",),
            source_root_files=(),
        )
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_report_path_outside_repository_is_refused_even_under_a_source_root(
    tmp_path,
):
    repo_top = tmp_path / "repo"
    run_cwd = repo_top / "app"
    outside_sources = tmp_path / "external-sources"
    run_cwd.mkdir(parents=True)
    outside_sources.mkdir()
    (run_cwd / "redirect.py").symlink_to(outside_sources / "mod.py")
    report = SimpleNamespace(sources={"redirect.py": object()})

    with pytest.raises(AssayError, match="resolves outside the snapshot repository") as caught:
        mutation._resolve_report_paths(
            report,
            run_cwd=run_cwd,
            repo_top=repo_top,
            source_root_paths=(outside_sources,),
            source_root_files=(),
        )

    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_report_path_through_symlink_file_root_requires_declared_lexical_key(
    tmp_path,
):
    repo_top = tmp_path / "repo"
    run_cwd = repo_top / "app"
    run_cwd.mkdir(parents=True)
    target = run_cwd / "target.py"
    target.write_text("value = 1\n", encoding="utf-8")
    declared = run_cwd / "declared.py"
    declared.symlink_to(target.name)

    with pytest.raises(
        AssayError, match="not under any declared judge.source_roots"
    ) as caught:
        mutation._resolve_report_paths(
            SimpleNamespace(sources={"target.py": object()}),
            run_cwd=run_cwd,
            repo_top=repo_top,
            source_root_paths=(declared.resolve(),),
            source_root_files=("app/declared.py",),
        )

    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_mutation_worker_propagates_non_timeout_post_execution_git_failure(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    job = _job()
    git_repo.write(job.path, job.original_text)
    git_repo.commit_all("seed mutation worker")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    failure = AssayError(
        "post-run Git inspection failed",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )
    monkeypatch.setattr(
        mutation,
        "_snapshot_left_dirt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    plan = make_plan(make_lane(argv=("check",)))

    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with pytest.raises(AssayError) as caught:
            mutation._execute_mutation_jobs(
                job_list=(job,),
                deadline=make_deadline(),
                jobs=1,
                prepared=prepared,
                plan=plan,
                process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
                    list(argv), returncode=0, stdout="", stderr=""
                ),
                clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
                write_progress=None,
                execute_plan=runner.execute_plan,
                resolve_run_cwd=lambda root, _plan: root,
                total=1,
                candidate_count=1,
            )

    assert caught.value is failure


def test_mutation_worker_leaves_candidate_unclassified_when_termination_interrupts_integrity_check(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    job = _job()
    git_repo.write(job.path, job.original_text)
    git_repo.commit_all("seed mutation worker")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    state_root = tmp_path / "state"
    progress: list[dict[str, object]] = []
    process_calls: list[tuple[str, ...]] = []
    integrity_checks: list[bool] = []
    timeout = AssayError(
        "Assay termination was requested",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def terminate_during_integrity(_job, _snapshot, *, remaining):
        (_snapshot.root / _job.path).write_text(
            "post-command snapshot mutation\n", encoding="utf-8"
        )
        integrity_checks.append(True)
        raise timeout

    def run(argv, *, env, cwd, timeout):
        process_calls.append(tuple(argv))
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mutation, "_snapshot_left_dirt", terminate_during_integrity)
    plan = make_plan(make_lane(argv=("check",)))

    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        result = mutation._execute_mutation_jobs(
            job_list=(job,),
            deadline=make_deadline(),
            jobs=1,
            prepared=prepared,
            plan=plan,
            process_runner=run,
            clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
            write_progress=progress.append,
            execute_plan=runner.execute_plan,
            resolve_run_cwd=lambda root, _plan: root,
            total=1,
            candidate_count=1,
            state_root=state_root,
            judge="j" * 64,
        )

    assert isinstance(result, Mutation)
    assert len(result.budget_exceeded) == 1
    assert not result.killed and not result.survived and not result.crashed
    assert integrity_checks == [True]
    assert process_calls
    assert not list(state_root.glob("*.json"))
    assert not any(event.get("event") == "candidate" for event in progress)


def test_mutation_worker_keeps_the_first_fatal_error_when_another_worker_fails(
):
    errors = [
        AssayError(
            f"worker {index} failed",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )
        for index in range(2)
    ]

    class Future(ConcurrentFuture):
        def __init__(self, error):
            super().__init__()
            self.set_exception(error)

    class Executor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, _function, position):
            return Future(errors[position])

    with pytest.raises(AssayError) as caught:
        mutation._execute_mutation_jobs(
            job_list=(_job(), _job()),
            deadline=make_deadline(),
            jobs=2,
            prepared=None,
            plan=make_plan(make_lane(argv=("check",))),
            process_runner=lambda *_args, **_kwargs: None,
            clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
            executor_factory=lambda _jobs: Executor(),
            write_progress=None,
            execute_plan=lambda *_args, **_kwargs: None,
            resolve_run_cwd=lambda root, _plan: root,
            total=2,
            candidate_count=2,
        )

    assert caught.value is errors[0]


def test_run_mutation_returns_unsupported_before_using_the_snapshot():
    target = mutation.MutationTarget(
        path="src/mod.py", text="value = 1\n", lines=frozenset({1})
    )

    class UnsupportedAdapter:
        def generate_mutation_sites(self, *_args, **_kwargs):
            return mutation.UNSUPPORTED

    result = mutation.run_mutation(
        **_run_mutation_args(targets=(target,), adapter=UnsupportedAdapter())
    )
    assert result == mutation.UNSUPPORTED


def test_run_mutation_stops_at_the_max_mutant_sentinel_before_execution():
    target = mutation.MutationTarget(
        path="src/mod.py", text="x = 1\ny = 2\n", lines=frozenset({1, 2})
    )
    sites = [
        mutation.MutationSite(
            start_byte=4,
            end_byte=5,
            replacement=b"3",
            lineno=1,
            operator="python:compare-swap",
            description="first",
        ),
        mutation.MutationSite(
            start_byte=10,
            end_byte=11,
            replacement=b"3",
            lineno=2,
            operator="python:compare-swap",
            description="second",
        ),
    ]
    sites.sort(key=lambda site: site.identity)

    class TwoSiteAdapter:
        def generate_mutation_sites(self, *_args, **_kwargs):
            return tuple(sites)

    result = mutation.run_mutation(
        **_run_mutation_args(
            targets=(target,),
            adapter=TwoSiteAdapter(),
            max_mutants=1,
        )
    )
    assert isinstance(result, Mutation)
    assert result.candidate_count == 2
    assert result.total == 0


def test_empty_mutation_shard_records_its_assignment_without_running_a_candidate(
    tmp_path,
):
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    text = "def flag():\n    return True\n"
    repo.write("src/mod.py", text)
    repo.commit_all("add one mutation site")
    lane = make_lane(argv=("pytest", "-q"))
    from assay.runner import execute_command

    baseline_result = execute_command(
        lane,
        cwd=repo.path,
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0
        ),
    )
    target = mutation.MutationTarget(
        path="src/mod.py", text=text, lines=frozenset({2})
    )
    sites = mutation.collect_mutation_sites(
        (target,),
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=2,
    )
    assert sites != mutation.UNSUPPORTED and len(sites) == 1
    candidate = mutation.candidate_id(sites[0])
    shard_zero = mutation.select_mutation_shard([candidate], index=0, count=2)
    empty_index = 1 if shard_zero else 0
    progress_events = []
    progress = mutation.ProgressStream(
        progress_events.append,
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        monotonic=lambda: 5.0,
    )

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = mutation.run_mutation(
            baseline=baseline_result,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=(target,),
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=lambda *_args, **_kwargs: pytest.fail(
                "an empty shard must submit no mutant"
            ),
            clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
            state_root=tmp_path / "state",
            shard_index=empty_index,
            shard_count=2,
            progress_stream=progress,
        )

    assert isinstance(result, Mutation)
    assert result.total == 0
    assert result.candidate_ids == ()
    assert any(
        event.get("event") == "shard" and event.get("selected_total") == 0
        for event in progress_events
    )


def _candidate_for(index, count):
    for value in range(10_000):
        candidate = f"{value:064x}"
        assigned = int.from_bytes(
            hashlib.blake2b(candidate.encode("ascii"), digest_size=4).digest(), "big"
        ) % count
        if assigned == index:
            return candidate
    raise AssertionError("could not construct a shard candidate")


def _shard(index=0, count=2, ids=None, **overrides):
    result = {
        "schema_version": mutation.MUTATION_STATE_SCHEMA_VERSION,
        "lane": "package",
        "commit": "c" * 40,
        "shard_index": index,
        "shard_count": count,
        "candidate_ids": [] if ids is None else ids,
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        ([], "cannot merge zero mutation shards"),
        ([_shard(schema_version=999)], "unsupported shard schema_version"),
        (
            [_shard(schema_version=mutation.MUTATION_STATE_SCHEMA_VERSION - 1)],
            f"unsupported shard schema_version {mutation.MUTATION_STATE_SCHEMA_VERSION - 1}; "
            f"expected {mutation.MUTATION_STATE_SCHEMA_VERSION}$",
        ),
        (
            [_shard(schema_version=0)],
            f"unsupported shard schema_version 0; expected {mutation.MUTATION_STATE_SCHEMA_VERSION}$",
        ),
        ([_shard(lane="")], "lane and commit must be non-empty"),
        ([_shard(commit=None)], "lane and commit must be non-empty"),
        ([_shard(shard_index=True)], "shard index and count must be integers"),
        ([_shard(shard_count=0)], "invalid shard pair"),
        ([_shard(shard_index=2, shard_count=2)], "invalid shard pair"),
        ([_shard(candidate_ids="not-an-array")], "candidate_ids must be an array"),
        ([_shard(ids=["not-a-digest"])], "64-character hexadecimal digest"),
        ([_shard(ids=["1" * 64])], "hashes to shard"),
        ([_shard(unknown=True)], "unknown keys"),
        ([{"schema_version": 1}], "missing keys"),
    ],
)
def test_shard_merge_rejects_each_malformed_manifest_shape(documents, message):
    with pytest.raises(MutationStateError, match=message):
        mutation.merge_mutation_shards(documents)


def test_shard_merge_requires_one_lane_and_commit_across_documents():
    docs = [
        _shard(
            index,
            2,
            [_candidate_for(index, 2)],
            lane="package" if index == 0 else "another",
        )
        for index in range(2)
    ]
    with pytest.raises(MutationStateError, match="one lane and commit"):
        mutation.merge_mutation_shards(docs)


def test_shard_merge_requires_one_shard_count_across_documents():
    docs = [
        _shard(0, 2, [_candidate_for(0, 2)]),
        _shard(1, 3, [_candidate_for(1, 3)]),
    ]
    with pytest.raises(MutationStateError, match="same shard_count"):
        mutation.merge_mutation_shards(docs)


@pytest.mark.parametrize(
    ("index", "count"),
    [(True, 2), (0, True), (0.0, 2), (0, 2.0)],
)
def test_shard_selection_rejects_booleans_and_non_integer_indices(index, count):
    with pytest.raises(ValueError, match="must be integers"):
        mutation.select_mutation_shard([], index=index, count=count)


def test_shard_merge_refuses_a_repeated_candidate_within_one_document():
    candidate = _candidate_for(0, 2)
    with pytest.raises(MutationStateError, match="repeats candidate"):
        mutation.merge_mutation_shards(
            [
                _shard(0, 2, [candidate, candidate]),
                _shard(1, 2),
            ]
        )
