"""B544: deterministic owner manifests for native R2 store combines."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import GitRepo
from test_flex_r2_campaigns import _STRICT_AB, _seed

from assay import cli, mutation, mutation_store
from assay.cli import main
from assay.errors import LaneConfigError, Outcome, ReasonCode
from test_mutation_judge_identity import _record

_JUDGE = "e" * 64
_OTHER_JUDGE = "a" * 64
_MANIFEST_SCHEMA = "assay-merge-manifest/1"


def _job(name: str = "first") -> mutation.MutantJob:
    source = f"def {name}():\n    return True\n"
    raw = source.encode("utf-8")
    start = raw.index(b"True")
    return mutation.MutantJob(
        path=f"pkg/{name}.py",
        original_text=source,
        site=mutation.MutationSite(
            start_byte=start,
            end_byte=start + 4,
            replacement=b"False",
            lineno=2,
            operator="python:bool-const-flip",
            description=f"{name} True->False",
        ),
    )


def _payload(
    job: mutation.MutantJob,
    *,
    judge: str = _JUDGE,
    bucket: str = "survived",
    **overrides: object,
) -> dict[str, object]:
    return _record(job, judge=judge, outcome_bucket=bucket, **overrides)


def _write(store: Path, payload: dict[str, object]) -> None:
    mutation_store._write_mutation_state_record(store, payload)


def _document(
    stores: list[tuple[Path | str, tuple[str, ...]]],
    *,
    judge: str = _JUDGE,
) -> dict[str, object]:
    return {
        "schema": _MANIFEST_SCHEMA,
        "judge_sha256": judge,
        "stores": [
            {"path": str(path), "candidates": list(candidates)}
            for path, candidates in stores
        ],
    }


def _manifest(
    root: Path,
    stores: list[tuple[Path | str, tuple[str, ...]]],
    *,
    judge: str = _JUDGE,
    name: str = "manifest.json",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(json.dumps(_document(stores, judge=judge)), encoding="utf-8")
    return path


def _args(
    manifest: Path,
    *,
    resume: bool = True,
    shard: str | None = None,
    only: Path | None = None,
    exclude: Path | None = None,
    candidates_file: Path | None = None,
    merge_shard_store: list[Path] | None = None,
    carry_over: list[Path] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        carry_over=carry_over,
        carry_forward=None,
        merge_state=None,
        merge_shard_store=merge_shard_store,
        merge_manifest=manifest,
        carry_over_reason=None,
        only_candidates=only,
        exclude_candidates=exclude,
        candidates_file=candidates_file,
        resume=resume,
        shard=shard,
    )


def _cli_flex(
    root: Path,
    args: SimpleNamespace,
    *,
    native_r2: bool = True,
) -> mutation.FlexOptions | None:
    return cli._resolve_flex_options(
        args,
        SimpleNamespace(name="unit"),
        SimpleNamespace(path=root / "assay.toml", project_root=root),
        native_r2=native_r2,
    )


def _request(
    jobs: tuple[mutation.MutantJob, ...],
    target: Path,
    *,
    campaign_deadline: str | None = None,
    events: list[dict[str, object]] | None = None,
) -> mutation_store.CombineRequest:
    return mutation_store.CombineRequest(
        jobs=jobs,
        judge=_JUDGE,
        state_root=target,
        state_root_fd=None,
        state_root_guard=None,
        campaign_deadline_sha256=campaign_deadline,
        context=mutation_store.RecordContext(),
        resume=True,
        rejudge_ids=frozenset(),
        rejudge_outcomes=frozenset(),
        resource_sha256=None,
        carry_over_reason=None,
        clock=lambda: datetime(2026, 11, 24, tzinfo=timezone.utc),
        deadline_check=lambda: 1.0,
        write_progress=(
            None if events is None else lambda event: events.append(dict(event))
        ),
        total=len(jobs),
    )


def _combine(
    manifest: Path,
    jobs: tuple[mutation.MutantJob, ...],
    target: Path,
    *,
    events: list[dict[str, object]] | None = None,
    campaign_deadline: str | None = None,
) -> mutation_store.CombineResult:
    return mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=manifest, policy="owner-manifest"
            ),
        ),
        _request(
            jobs,
            target,
            events=events,
            campaign_deadline=campaign_deadline,
        ),
    )


def _record_bytes(store: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(store.glob("*.json"))
    }


def _run_campaign(
    repo: GitRepo,
    state: Path,
    name: str,
    *extra: str,
    resume: bool = True,
) -> tuple[int, list[dict[str, object]], str, Path]:
    progress = state.parent / f"{name}.jsonl"
    verdict = state.parent / f"{name}-verdict.json"
    out = io.StringIO()
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
            *(["--resume"] if resume else []),
            "--verdict-json",
            str(verdict),
            *extra,
        ],
        stdout=out,
        stderr=err,
    )
    events = (
        [
            json.loads(line)
            for line in progress.read_text(encoding="utf-8").splitlines()
        ]
        if progress.exists()
        else []
    )
    return code, events, err.getvalue(), verdict


def _candidate_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [event for event in events if event.get("event") == "candidate"]


def _split_serial_records(
    serial: Path,
    first: Path,
    second: Path,
) -> tuple[str, ...]:
    records = sorted(serial.glob("*.json"))
    assert len(records) == 2
    first.mkdir()
    second.mkdir()
    for target, record in zip((first, second), records, strict=True):
        (target / record.name).write_bytes(record.read_bytes())
    return tuple(record.stem for record in records)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"assay-merge-manifest/1","schema":"assay-merge-manifest/1",'
        b'"judge_sha256":"' + _JUDGE.encode() + b'","stores":[]}',
        b'{"schema":"assay-merge-manifest/1","judge_sha256":"' + _JUDGE.encode()
        + b'","stores":NaN}',
        b"\xffnot-utf8",
        b"[]",
    ],
)
def test_malformed_manifest_variants_use_the_one_token(
    tmp_path: Path, raw: bytes
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(raw)

    with pytest.raises(LaneConfigError) as caught:
        mutation_store.load_merge_manifest(manifest)
    assert "merge manifest refused: malformed-manifest:" in str(caught.value)


@pytest.mark.parametrize(
    "document",
    [
        {"schema": _MANIFEST_SCHEMA, "judge_sha256": _JUDGE},
        {
            "schema": "wrong",
            "judge_sha256": _JUDGE,
            "stores": [],
        },
        {"schema": _MANIFEST_SCHEMA, "judge_sha256": "A" * 64, "stores": []},
        {"schema": _MANIFEST_SCHEMA, "judge_sha256": _JUDGE, "stores": {}},
        {
            "schema": _MANIFEST_SCHEMA,
            "judge_sha256": _JUDGE,
            "stores": [{"path": "", "candidates": []}],
        },
        {
            "schema": _MANIFEST_SCHEMA,
            "judge_sha256": _JUDGE,
            "stores": [{"path": "x", "candidates": "not-a-list"}],
        },
        {
            "schema": _MANIFEST_SCHEMA,
            "judge_sha256": _JUDGE,
            "stores": [{"path": "x", "candidates": [], "extra": True}],
        },
        {
            "schema": _MANIFEST_SCHEMA,
            "judge_sha256": _JUDGE,
            "stores": [{"path": "x", "candidates": ["not-an-id"]}],
        },
    ],
)
def test_schema_and_shape_errors_are_malformed_manifest(
    tmp_path: Path, document: dict[str, object]
) -> None:
    store = tmp_path / "store"
    store.mkdir()
    for entry in document.get("stores", []):
        if isinstance(entry, dict) and entry.get("path") == "x":
            entry["path"] = str(store)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(LaneConfigError, match="malformed-manifest"):
        mutation_store.load_merge_manifest(manifest)


def test_a_missing_manifest_is_a_malformed_manifest(tmp_path: Path) -> None:
    with pytest.raises(LaneConfigError, match="malformed-manifest"):
        mutation_store.load_merge_manifest(tmp_path / "absent.json")


def test_a_symlinked_manifest_is_malformed_not_followed(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(
        json.dumps(_document([], judge=_JUDGE)),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.symlink_to(real)

    with pytest.raises(LaneConfigError, match="malformed-manifest"):
        mutation_store.load_merge_manifest(manifest)


def test_a_relative_manifest_path_is_anchored_by_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest(tmp_path, [], name="relative-manifest.json")
    monkeypatch.chdir(tmp_path)

    loaded = mutation_store.load_merge_manifest(Path("relative-manifest.json"))

    assert loaded.path == tmp_path / "relative-manifest.json"


def test_the_manifest_read_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"x" * 17)
    monkeypatch.setattr(mutation_store, "MERGE_MANIFEST_LIMIT", 16)

    with pytest.raises(LaneConfigError, match="malformed-manifest"):
        mutation_store.load_merge_manifest(manifest)


def test_missing_store_is_refused_by_name(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [(tmp_path / "missing", ("0" * 64,))],
    )

    for operation in (
        lambda: mutation_store.load_merge_manifest(manifest),
        lambda: _cli_flex(tmp_path, _args(manifest)),
    ):
        with pytest.raises(LaneConfigError, match="missing-store"):
            operation()


def test_duplicate_store_is_refused_after_path_resolution(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    manifest = _manifest(
        tmp_path,
        [(store, ()), (store / ".", ())],
    )

    for operation in (
        lambda: mutation_store.load_merge_manifest(manifest),
        lambda: _cli_flex(tmp_path, _args(manifest)),
    ):
        with pytest.raises(LaneConfigError, match="duplicate-store"):
            operation()


def test_store_paths_resolve_relative_to_the_manifest_directory(tmp_path: Path) -> None:
    manifest_directory = tmp_path / "manifests"
    store = manifest_directory / "stores" / "first"
    store.mkdir(parents=True)
    manifest = _manifest(
        manifest_directory,
        [("stores/first", ())],
    )

    loaded = mutation_store.load_merge_manifest(manifest)

    assert loaded.stores == (
        mutation_store.MergeManifestStore(path=store, candidates=()),
    )


def test_duplicate_owner_across_stores_is_refused(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    candidate = mutation.candidate_id(_job())
    manifest = _manifest(
        tmp_path,
        [(first, (candidate,)), (second, (candidate,))],
    )

    for operation in (
        lambda: mutation_store.load_merge_manifest(manifest),
        lambda: _cli_flex(tmp_path, _args(manifest)),
    ):
        with pytest.raises(LaneConfigError, match="duplicate-owner"):
            operation()


def test_duplicate_owner_inside_one_store_is_refused(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    candidate = mutation.candidate_id(_job())
    manifest = _manifest(tmp_path, [(store, (candidate, candidate))])

    with pytest.raises(LaneConfigError, match="duplicate-owner"):
        mutation_store.load_merge_manifest(manifest)


def test_problem_list_is_sorted_and_bounded_to_twenty(tmp_path: Path) -> None:
    stores = [
        (tmp_path / f"missing-{index:02d}", ())
        for index in range(25)
    ]
    manifest = _manifest(tmp_path, stores)

    with pytest.raises(LaneConfigError) as caught:
        mutation_store.load_merge_manifest(manifest)
    message = str(caught.value)
    assert message.count("missing-store:") == 20
    assert "missing-00" in message
    assert "missing-20" not in message


def test_two_store_merge_bytes_equal_a_serial_store(tmp_path: Path) -> None:
    first_job = _job("first")
    second_job = _job("second")
    first_store = tmp_path / "first"
    second_store = tmp_path / "second"
    first_record = _payload(first_job, bucket="killed")
    second_record = _payload(second_job, bucket="survived")
    _write(first_store, first_record)
    _write(second_store, second_record)
    manifest = _manifest(
        tmp_path,
        [
            (first_store, (mutation.candidate_id(first_job),)),
            (second_store, (mutation.candidate_id(second_job),)),
        ],
    )
    merged = tmp_path / "merged"
    serial = tmp_path / "serial"
    _write(serial, first_record)
    _write(serial, second_record)

    events: list[dict[str, object]] = []
    result = _combine(manifest, (first_job, second_job), merged, events=events)

    assert _record_bytes(merged) == _record_bytes(serial)
    assert len(result.records) == 2
    merge_event = next(event for event in events if event["event"] == "merge_manifest")
    assert merge_event == {
        "event": "merge_manifest",
        "candidate_total": 2,
        "owners": 2,
        "non_owner_duplicates": 0,
        "unplanned_records": 0,
        "target_replaced": 0,
    }


def test_real_cli_owner_manifest_matches_serial_bytes_and_replaces_target(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    _seed(git_repo, _STRICT_AB, sites=2)
    serial = tmp_path / "serial"
    code, serial_events, err, _verdict = _run_campaign(
        git_repo, serial, "serial"
    )
    assert code == 0, err
    judge = next(
        event["judge_sha256"]
        for event in serial_events
        if event.get("event") == "candidates"
    )
    assert isinstance(judge, str)

    first = tmp_path / "first"
    second = tmp_path / "second"
    candidates = _split_serial_records(serial, first, second)
    manifest = _manifest(
        tmp_path,
        [
            (first, (candidates[0],)),
            (second, (candidates[1],)),
        ],
        judge=judge,
        name="owner-manifest.json",
    )

    merged = tmp_path / "merged"
    target_record = json.loads(
        (serial / f"{candidates[0]}.json").read_text(encoding="utf-8")
    )
    target_record["worker_count"] = 7
    _write(merged, target_record)

    code, events, err, verdict = _run_campaign(
        git_repo,
        merged,
        "merged",
        "--merge-manifest",
        str(manifest),
    )
    assert code == 0, err
    assert _candidate_events(events) == []
    assert _record_bytes(merged) == _record_bytes(serial)
    assert json.loads(verdict.read_text(encoding="utf-8"))["outcome"] == "PASS"

    merge_event = next(
        event for event in events if event.get("event") == "merge_manifest"
    )
    assert merge_event["owners"] == 2
    assert merge_event["non_owner_duplicates"] == 0
    assert merge_event["unplanned_records"] == 0
    assert merge_event["target_replaced"] == 1
    event_names = [event.get("event") for event in events]
    assert event_names.index("merge_manifest") < event_names.index("resume")


def test_non_owner_duplicate_and_unplanned_record_are_ignored_and_counted(
    tmp_path: Path,
) -> None:
    first_job = _job("first")
    second_job = _job("second")
    unplanned_job = _job("unplanned")
    first_store = tmp_path / "first"
    second_store = tmp_path / "second"
    owner = _payload(first_job, bucket="survived")
    _write(first_store, owner)
    _write(second_store, _payload(second_job))
    duplicate = _payload(
        first_job,
        bucket="killed",
        imported_at="2020-01-01T00:00:00Z",
    )
    _write(second_store, duplicate)
    _write(first_store, _payload(unplanned_job))
    duplicate_path = second_store / f"{mutation.candidate_id(first_job)}.json"
    os.utime(duplicate_path, (1, 1))
    manifest = _manifest(
        tmp_path,
        [
            (first_store, (mutation.candidate_id(first_job),)),
            (second_store, (mutation.candidate_id(second_job),)),
        ],
    )
    events: list[dict[str, object]] = []

    result = _combine(manifest, (first_job, second_job), tmp_path / "target", events=events)

    merge_event = next(event for event in events if event["event"] == "merge_manifest")
    assert merge_event["non_owner_duplicates"] == 1
    assert merge_event["unplanned_records"] == 1
    assert result.records[0]["outcome_bucket"] == "survived"
    assert result.records[0]["candidate_id"] == mutation.candidate_id(first_job)


def test_two_merges_are_byte_and_event_deterministic(tmp_path: Path) -> None:
    job = _job()
    source = tmp_path / "source"
    _write(source, _payload(job, bucket="killed"))
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])
    first_events: list[dict[str, object]] = []
    second_events: list[dict[str, object]] = []

    _combine(manifest, (job,), tmp_path / "first-target", events=first_events)
    _combine(manifest, (job,), tmp_path / "second-target", events=second_events)

    assert _record_bytes(tmp_path / "first-target") == _record_bytes(
        tmp_path / "second-target"
    )
    assert first_events == second_events


def test_owner_manifest_materializes_without_a_progress_stream(tmp_path: Path) -> None:
    job = _job()
    source = tmp_path / "source"
    _write(source, _payload(job))
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])
    target = tmp_path / "target"

    result = mutation_store.combine(
        (
            mutation_store.StoreSource(
                path=manifest,
                policy="owner-manifest",
            ),
        ),
        _request((job,), target),
    )

    assert result.records[0]["candidate_id"] == mutation.candidate_id(job)
    assert (target / f"{mutation.candidate_id(job)}.json").exists()


def test_owner_record_replaces_a_differing_same_judge_target(
    tmp_path: Path,
) -> None:
    job = _job()
    source = tmp_path / "source"
    target = tmp_path / "target"
    owner = _payload(job, bucket="survived")
    _write(source, owner)
    _write(target, _payload(job, bucket="killed"))
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])
    events: list[dict[str, object]] = []

    result = _combine(manifest, (job,), target, events=events)

    merge_event = next(event for event in events if event["event"] == "merge_manifest")
    assert merge_event["target_replaced"] == 1
    assert result.records[0]["outcome_bucket"] == "survived"
    assert (target / f"{mutation.candidate_id(job)}.json").read_bytes() == (
        mutation_store._canonical_state_record_bytes(owner)
    )


def test_identical_and_foreign_target_records_are_not_counted_as_replacements(
    tmp_path: Path,
) -> None:
    job = _job()
    source = tmp_path / "source"
    owner = _payload(job, bucket="survived")
    _write(source, owner)
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])

    identical = tmp_path / "identical"
    _write(identical, owner)
    events: list[dict[str, object]] = []
    _combine(manifest, (job,), identical, events=events)
    assert events[-1]["target_replaced"] == 0

    foreign = tmp_path / "foreign"
    _write(foreign, _payload(job, judge=_OTHER_JUDGE))
    events.clear()
    _combine(manifest, (job,), foreign, events=events)
    assert events[-1]["target_replaced"] == 0


def test_an_unparseable_target_record_is_overwritten_without_replacement_count(
    tmp_path: Path,
) -> None:
    job = _job()
    candidate = mutation.candidate_id(job)
    source = tmp_path / "source"
    _write(source, _payload(job))
    manifest = _manifest(tmp_path, [(source, (candidate,))])
    target = tmp_path / "target"
    target.mkdir()
    (target / f"{candidate}.json").write_text("not-json", encoding="utf-8")
    events: list[dict[str, object]] = []

    result = _combine(manifest, (job,), target, events=events)

    assert result.records[0]["candidate_id"] == candidate
    assert events[-1]["target_replaced"] == 0


def test_generations_are_never_consulted(tmp_path: Path) -> None:
    job = _job()
    source = tmp_path / "source"
    generation = source / "judges" / _JUDGE
    generation.mkdir(parents=True)
    payload = _payload(job)
    (generation / f"{mutation.candidate_id(job)}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])

    with pytest.raises(mutation_store.MergeManifestError, match="missing-owner-record"):
        _combine(manifest, (job,), tmp_path / "target")


@pytest.mark.parametrize(
    "token",
    [
        "unknown-candidate",
        "unowned-candidate",
        "judge-mismatch",
        "missing-owner-record",
        "invalid-owner-record",
    ],
)
def test_runtime_refusal_is_bad_lane_config_exit_two(
    tmp_path: Path, token: str
) -> None:
    job = _job()
    source = tmp_path / token
    source.mkdir()
    candidate = mutation.candidate_id(job)
    unknown = "f" * 64
    if token == "unknown-candidate":
        stores = [(source, (candidate, unknown))]
    elif token == "unowned-candidate":
        stores = []
    elif token == "judge-mismatch":
        stores = [(source, (candidate,))]
    elif token == "missing-owner-record":
        stores = [(source, (candidate,))]
    else:
        stores = [(source, (candidate,))]
        (source / f"{candidate}.json").write_text("not-json", encoding="utf-8")
    manifest = _manifest(tmp_path, stores, judge=_OTHER_JUDGE if token == "judge-mismatch" else _JUDGE)

    with pytest.raises(mutation_store.MergeManifestError, match=token) as caught:
        _combine(manifest, (job,), tmp_path / "target")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert caught.value.exit_code == 2


def test_unreadable_and_rejected_owner_records_are_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    candidate = mutation.candidate_id(job)
    source = tmp_path / "source"
    source.mkdir()
    manifest = _manifest(tmp_path, [(source, (candidate,))])
    assignment = source / f"{candidate}.json"
    replacement = tmp_path / "real-record.json"
    _write(tmp_path / "record-store", _payload(job))
    source_record = tmp_path / "record-store" / f"{candidate}.json"
    replacement.write_bytes(source_record.read_bytes())
    assignment.symlink_to(replacement)

    with pytest.raises(mutation_store.MergeManifestError, match="invalid-owner-record"):
        _combine(manifest, (job,), tmp_path / "symlink-target")

    assignment.unlink()
    (source / f"{candidate}.json").write_text(
        json.dumps({**_payload(job), "schema_version": 999}),
        encoding="utf-8",
    )
    original_load = mutation_store._load_validated_state_record
    loaded: list[object] = []

    def rejected(*args: object, **kwargs: object):
        loaded.append((args, kwargs))
        return mutation_store._RECORD_REJECTED

    monkeypatch.setattr(mutation_store, "_load_validated_state_record", rejected)
    with pytest.raises(mutation_store.MergeManifestError, match="invalid-owner-record"):
        _combine(manifest, (job,), tmp_path / "rejected-target")
    assert loaded
    assert mutation_store._load_validated_state_record is not original_load


def test_owner_load_error_and_target_read_error_are_bounded_refusals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    candidate = mutation.candidate_id(job)
    source = tmp_path / "source"
    _write(source, _payload(job))
    manifest = _manifest(tmp_path, [(source, (candidate,))])

    def invalid(*args: object, **kwargs: object):
        raise mutation_store.MutationStateError("deliberately invalid")

    monkeypatch.setattr(mutation_store, "_load_validated_state_record", invalid)
    with pytest.raises(mutation_store.MergeManifestError, match="invalid-owner-record"):
        _combine(manifest, (job,), tmp_path / "invalid-target")

    def unreadable(*args: object, **kwargs: object):
        raise mutation_store.MutationStateError("deliberately unreadable")

    monkeypatch.setattr(mutation_store, "_load_validated_state_record", lambda *a, **k: _payload(job))
    monkeypatch.setattr(mutation_store, "_read_state_record_bytes", unreadable)
    events: list[dict[str, object]] = []
    _combine(manifest, (job,), tmp_path / "unreadable-target", events=events)
    assert events[-1]["target_replaced"] == 0


def test_scandir_failure_after_proof_contributes_no_ignored_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = _job()
    source = tmp_path / "source"
    _write(source, _payload(job))
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])

    def fail(_path: Path):
        raise OSError("simulated directory replacement")

    monkeypatch.setattr(mutation_store.os, "scandir", fail)
    events: list[dict[str, object]] = []
    result = _combine(manifest, (job,), tmp_path / "target", events=events)
    assert result.records[0]["candidate_id"] == mutation.candidate_id(job)
    assert events[-1]["non_owner_duplicates"] == 0
    assert events[-1]["unplanned_records"] == 0


def test_manifest_requires_native_r2_and_resume_after_old_checks(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, [])
    with pytest.raises(LaneConfigError, match="requires a native R2"):
        _cli_flex(tmp_path, _args(manifest), native_r2=False)
    with pytest.raises(LaneConfigError, match="--merge-manifest requires --resume"):
        _cli_flex(tmp_path, _args(manifest, resume=False))


def test_manifest_conflicts_c2_c3_c4(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [])
    with pytest.raises(LaneConfigError, match="cannot be combined with --shard"):
        _cli_flex(tmp_path, _args(manifest, shard="0/2"))
    only = tmp_path / "only.txt"
    only.write_text("", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="cannot be combined with --shard"):
        _cli_flex(tmp_path, _args(manifest, only=only))
    shard_store = tmp_path / "shard"
    shard_store.mkdir()
    with pytest.raises(LaneConfigError, match="--merge-shard-store"):
        _cli_flex(
            tmp_path,
            _args(manifest, merge_shard_store=[shard_store]),
        )
    with pytest.raises(LaneConfigError, match="--candidates-file"):
        _cli_flex(
            tmp_path,
            _args(manifest, candidates_file=tmp_path / "candidates"),
        )


def test_manifest_file_must_exist(tmp_path: Path) -> None:
    with pytest.raises(LaneConfigError, match="is not an existing file"):
        _cli_flex(tmp_path, _args(tmp_path / "absent.json"))


def test_an_old_flag_refusal_still_wins(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [])
    old = [
        "--carry-over/--carry-forward/--only-candidates/--exclude-candidates "
        "require a native R2 mutation lane"
    ]
    carry = tmp_path / "carry"
    carry.mkdir()
    with pytest.raises(LaneConfigError) as caught:
        _cli_flex(
            tmp_path,
            _args(manifest, carry_over=[carry]),
            native_r2=False,
        )
    assert str(caught.value) == old[0]


def test_every_refusal_token_is_named_by_the_real_cli_before_candidates(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    _seed(git_repo, _STRICT_AB, sites=2)
    serial = tmp_path / "serial"
    code, events, err, _verdict = _run_campaign(
        git_repo, serial, "serial"
    )
    assert code == 0, err
    judge = next(
        event["judge_sha256"]
        for event in events
        if event.get("event") == "candidates"
    )
    assert isinstance(judge, str)
    candidates = tuple(path.stem for path in sorted(serial.glob("*.json")))
    assert len(candidates) == 2
    unknown = next(
        digest
        for digest in ("f" * 64, "0" * 64, "1" * 64)
        if digest not in candidates
    )

    first = tmp_path / "owned-first"
    second = tmp_path / "owned-second"
    first.mkdir()
    second.mkdir()
    malformed = tmp_path / "malformed.json"
    malformed.write_bytes(b"\xff")

    cases: dict[str, Path] = {
        "malformed-manifest": malformed,
        "missing-store": _manifest(
            tmp_path / "missing-store-case",
            [(tmp_path / "absent-store", (candidates[0],))],
            judge=judge,
        ),
        "duplicate-store": _manifest(
            tmp_path / "duplicate-store-case",
            [(first, ()), (first, ())],
            judge=judge,
        ),
        "duplicate-owner": _manifest(
            tmp_path / "duplicate-owner-case",
            [(first, (candidates[0],)), (second, (candidates[0],))],
            judge=judge,
        ),
        "unknown-candidate": _manifest(
            tmp_path / "unknown-candidate-case",
            [
                (first, (candidates[0],)),
                (second, (candidates[1], unknown)),
            ],
            judge=judge,
        ),
        "unowned-candidate": _manifest(
            tmp_path / "unowned-candidate-case",
            [],
            judge=judge,
        ),
        "judge-mismatch": _manifest(
            tmp_path / "judge-mismatch-case",
            [
                (first, (candidates[0],)),
                (second, (candidates[1],)),
            ],
            judge="0" * 64 if judge != "0" * 64 else "1" * 64,
        ),
        "missing-owner-record": _manifest(
            tmp_path / "missing-owner-case",
            [
                (first, (candidates[0],)),
                (second, (candidates[1],)),
            ],
            judge=judge,
        ),
        "invalid-owner-record": _manifest(
            tmp_path / "invalid-owner-case",
            [
                (first, (candidates[0],)),
                (second, (candidates[1],)),
            ],
            judge=judge,
        ),
    }

    for token, manifest in cases.items():
        case = tmp_path / f"run-{token}"
        case.mkdir()
        for store in (first, second):
            for path in store.glob("*.json"):
                path.unlink()
        if token in {
            "duplicate-owner",
            "unknown-candidate",
            "judge-mismatch",
            "missing-owner-record",
            "invalid-owner-record",
        }:
            (first / f"{candidates[0]}.json").write_bytes(
                (serial / f"{candidates[0]}.json").read_bytes()
            )
        if token in {
            "unknown-candidate",
            "judge-mismatch",
            "invalid-owner-record",
        }:
            (second / f"{candidates[1]}.json").write_bytes(
                (serial / f"{candidates[1]}.json").read_bytes()
            )
        if token == "invalid-owner-record":
            (second / f"{candidates[1]}.json").write_text(
                "not-json", encoding="utf-8"
            )

        code, refused_events, err, _verdict = _run_campaign(
            git_repo,
            case / "target",
            token,
            "--merge-manifest",
            str(manifest),
        )
        assert code == 2, err
        assert f"merge manifest refused: {token}:" in err
        assert _candidate_events(refused_events) == []


def test_campaign_deadline_mismatch_is_invalid_owner_record(tmp_path: Path) -> None:
    job = _job()
    source = tmp_path / "source"
    _write(source, _payload(job))
    manifest = _manifest(tmp_path, [(source, (mutation.candidate_id(job),))])

    with pytest.raises(mutation_store.MergeManifestError, match="invalid-owner-record"):
        _combine(
            manifest,
            (job,),
            tmp_path / "target",
            campaign_deadline="c" * 64,
        )


def test_owner_judge_mismatch_and_missing_judge_are_distinct_tokens(
    tmp_path: Path,
) -> None:
    job = _job()
    candidate = mutation.candidate_id(job)
    manifest = _manifest(tmp_path, [(tmp_path / "source", (candidate,))])
    source = tmp_path / "source"
    source.mkdir()

    _write(source, _payload(job, judge=_OTHER_JUDGE))
    with pytest.raises(mutation_store.MergeManifestError, match="judge-mismatch"):
        _combine(manifest, (job,), tmp_path / "foreign-target")

    payload = _payload(job)
    payload.pop("judge_sha256")
    _write(source, payload)
    with pytest.raises(
        mutation_store.MergeManifestError, match="invalid-owner-record"
    ):
        _combine(manifest, (job,), tmp_path / "judge-less-target")


def test_merge_manifest_is_a_whole_lane_bad_lane_config_refusal() -> None:
    error = mutation_store.MergeManifestError(
        "merge manifest refused: unknown-candidate: detail"
    )
    assert isinstance(error, mutation.InvalidCandidateSelectionError)
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert error.exit_code == 2
