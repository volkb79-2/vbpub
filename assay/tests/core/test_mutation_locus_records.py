"""WP2: optional locus metadata and carry_over schema 2."""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import GitRepo
from test_flex_r2_campaigns import (
    _BLIND,
    _LANE,
    _RUN_GATE,
    _event,
    _records,
    _run,
    _seed,
)
from test_mutation_judge_identity import _record as _state_record
from test_mutation_judge_identity import _store

from assay import mutation
from assay.candidate_locus import LOCUS_SCHEMA, Locus
from assay.mutation import MutationStateError

_JUDGE = "e" * 64
_OLD_JUDGE = "a" * 64
_CURRENT_JUDGE = "b" * 64
_FUNCTION_SOURCE = "def flags():\n    return True\n"
_ABSENT = object()


def _function_job() -> mutation.MutantJob:
    source = _FUNCTION_SOURCE.encode("utf-8")
    start = source.index(b"True")
    end = start + len(b"True")
    site = mutation.MutationSite(
        start_byte=start,
        end_byte=end,
        replacement=b"False",
        lineno=2,
        operator="python:bool-const-flip",
        description="True->False",
    )
    return mutation.MutantJob(
        path="pkg/flags.py",
        original_text=_FUNCTION_SOURCE,
        site=site,
    )


def _first_and_second_jobs() -> tuple[mutation.MutantJob, mutation.MutantJob]:
    text = "def first():\n    return True\ndef second():\n    return True\n"
    source = text.encode("utf-8")
    first_start = source.index(b"True")
    second_start = source.index(b"True", first_start + len(b"True"))
    jobs = []
    for name, start, lineno in (
        ("first", first_start, 2),
        ("second", second_start, 4),
    ):
        jobs.append(mutation.MutantJob(
            path="pkg/flags.py",
            original_text=text,
            site=mutation.MutationSite(
                start_byte=start,
                end_byte=start + len(b"True"),
                replacement=b"False",
                lineno=lineno,
                operator="python:bool-const-flip",
                description=f"{name} True->False",
            ),
        ))
    return jobs[0], jobs[1]


def _locus_fields(job: mutation.MutantJob) -> dict[str, object]:
    source = job.original_text.encode("utf-8")
    return mutation._locus_fields(
        path=job.path,
        source=source,
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        operator=job.site.operator,
        replacement_sha256=job.site.replacement_sha256,
    )


def _record(job: mutation.MutantJob, *, locus: bool = True):
    source = job.original_text.encode("utf-8")
    payload = _state_record(
        job,
        judge=_JUDGE,
        outcome_bucket="killed",
        start_byte=job.site.start_byte,
        end_byte=job.site.end_byte,
        lineno=job.site.lineno,
        description=job.site.description,
        mutated_file_sha256=hashlib.sha256(job.site.apply(source)).hexdigest(),
    )
    if locus:
        payload.update(_locus_fields(job))
    return payload


def _seed_function(repo: GitRepo) -> None:
    judge = "grep -q 'return False' pkg/flags.py && exit 1\nexit 0\n"
    repo.write("assay.toml", _LANE)
    repo.write("run-gate.toml", _RUN_GATE)
    repo.write("pkg/flags.py", _FUNCTION_SOURCE)
    repo.write("tests/judge.sh", judge)
    repo.commit_all("function lane")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", _FUNCTION_SOURCE.replace("True", "False"))
    repo.commit_all("base flags")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", _FUNCTION_SOURCE)
    repo.commit_all("restore flags")


def _carry_v1(judge: str) -> dict[str, object]:
    return {
        "schema_version": mutation.CARRY_OVER_SCHEMA_VERSION,
        "from_judge_sha256": judge,
        "into_judge_sha256": judge,
        "source": "/prior/store",
        "reason": "schema-1 compatibility",
        "imported_at": "2026-11-17T00:00:00Z",
        "importer_version": "12.2.0",
    }


def _carry_v2(**overrides: object) -> dict[str, object]:
    value = {
        "schema_version": mutation.CARRY_OVER_SCHEMA_VERSION_V2,
        "into_judge_sha256": _CURRENT_JUDGE,
        "source": "/prior/store",
        "reason": "schema-2 provenance",
        "imported_at": "2026-11-18T00:00:00Z",
        "importer_version": "12.3.0",
    }
    value.update(overrides)
    return value


def _provenance(candidate: str = _JUDGE, **overrides: object) -> dict[str, object]:
    value = {
        "kind": "imported",
        "from_candidate_id": candidate,
        "from_judge_sha256": _JUDGE,
        "match": "exact-id",
        "reason": "schema-2 provenance",
    }
    value.update(overrides)
    return value


def test_provenance_constructor_owns_the_optional_fields():
    imported = mutation._provenance_record(
        kind="imported",
        from_candidate_id=_JUDGE,
        from_judge_sha256=_JUDGE,
        match="exact-id",
    )
    replayed = mutation._provenance_record(
        kind="replayed",
        from_candidate_id=_JUDGE,
        from_judge_sha256=_JUDGE,
        match="hint",
        reason="replay",
        replay_node_id="node-1",
    )
    assert set(imported) == {
        "kind", "from_candidate_id", "from_judge_sha256", "match"
    }
    assert replayed["reason"] == "replay"
    assert replayed["replay_node_id"] == "node-1"
    assert mutation.record_provenance(None) is None  # type: ignore[arg-type]
    assert mutation.record_provenance({
        "candidate_id": "not-an-id",
        "carry_over": _carry_v1(_JUDGE),
    }) is None
    schema_one_plus_provenance = {
        "judge_sha256": _JUDGE,
        "carry_over": _carry_v1(_JUDGE),
        "provenance": _provenance(),
    }
    assert mutation.record_provenance(schema_one_plus_provenance) is None
    assert mutation.record_effective_judge(schema_one_plus_provenance) is None
    locus_match = _provenance(_OLD_JUDGE, match="locus")
    assert mutation.record_provenance({
        "candidate_id": _JUDGE,
        "provenance": locus_match,
    }) == locus_match
    assert mutation.record_effective_judge({
        "judge_sha256": _OLD_JUDGE,
        "carry_over": _carry_v1(_JUDGE),
    }) is None
    assert mutation.record_effective_judge({"judge_sha256": ""}) is None
    assert mutation.record_effective_judge({"judge_sha256": 7}) is None


def test_new_candidate_records_and_progress_events_carry_locus_and_window(
    git_repo: GitRepo, tmp_path
):
    _seed_function(git_repo)
    code, events = _run(git_repo, tmp_path / "state", tmp_path / "progress.jsonl")
    assert code == 0
    candidate = _event(events, "candidate")
    assert candidate is not None
    assert candidate["event"] in mutation.PROGRESS_EVENTS
    (record,) = _records(tmp_path / "state")
    fields = _locus_fields(_function_job())
    assert fields.keys() == {"locus", "window_key"}
    assert candidate["locus"] == record["locus"] == fields["locus"]
    assert candidate["window_key"] == record["window_key"] == fields["window_key"]
    assert candidate["locus"]["qualname"] == "flags"
    assert "provenance" not in record and "carry_over" not in record


def test_module_level_fallback_writes_neither_locus_field(
    git_repo: GitRepo, tmp_path
):
    _seed(git_repo, _BLIND)
    code, events = _run(git_repo, tmp_path / "state", tmp_path / "progress.jsonl")
    assert code == 1
    candidate = _event(events, "candidate")
    assert candidate is not None
    (record,) = _records(tmp_path / "state")
    assert "locus" not in candidate and "window_key" not in candidate
    assert "locus" not in record and "window_key" not in record


def test_old_record_without_locus_loads_unchanged(tmp_path):
    job = _function_job()
    payload = _record(job, locus=False)
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert loaded == payload


def test_valid_locus_and_window_load_unchanged(tmp_path):
    job = _function_job()
    payload = _record(job)
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert loaded == payload
    assert loaded["locus"] == _locus_fields(job)["locus"]


@pytest.mark.parametrize("swap_locus", [False, True])
def test_swapped_span_drops_locus_and_record_remains_usable(
    tmp_path, swap_locus: bool
):
    job, other = _first_and_second_jobs()
    payload = _record(job)
    swapped = _record(other)
    for key in ("start_byte", "end_byte", "lineno"):
        payload[key] = swapped[key]
    if swap_locus:
        for key in ("description", "locus", "window_key"):
            payload[key] = swapped[key]
    root = _store(tmp_path, job, payload)
    locus_status: list[bool] = []

    loaded = mutation._load_validated_state_record(
        root, job, judge=_JUDGE, locus_status=locus_status
    )

    assert isinstance(loaded, dict)
    assert loaded["candidate_id"] == mutation.candidate_id(job)
    assert "locus" not in loaded and "window_key" not in loaded
    assert locus_status == [True]


def test_record_locus_rejects_an_operator_that_does_not_bind_the_job(tmp_path):
    job, _other = _first_and_second_jobs()
    payload = _record(job)
    payload["operator"] = "python:compare-swap"
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale operator"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


@pytest.mark.parametrize(
    "bad_locus",
    [
        {},
        None,
        {"schema": LOCUS_SCHEMA},
        {
            "schema": LOCUS_SCHEMA,
            "key": "a" * 64,
            "qualname": "flags",
            "segment_sha256": "b" * 64,
            "rel_start": 0,
            "rel_end": 4,
            "extra": True,
        },
    ],
)
def test_present_locus_with_wrong_shape_is_dropped(tmp_path, bad_locus):
    job = _function_job()
    payload = _record(job)
    payload["locus"] = bad_locus
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert isinstance(loaded, dict)
    assert "locus" not in loaded and "window_key" not in loaded


def test_window_key_must_match_locus(tmp_path):
    job = _function_job()
    payload = _record(job)
    payload["window_key"] = "f" * 64
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert isinstance(loaded, dict)
    assert "locus" not in loaded and "window_key" not in loaded


def test_window_key_without_locus_is_dropped(tmp_path):
    job = _function_job()
    payload = _record(job, locus=False)
    payload["window_key"] = "f" * 64
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert isinstance(loaded, dict)
    assert "locus" not in loaded and "window_key" not in loaded


def test_locus_on_an_exact_id_fallback_site_is_dropped(tmp_path):
    text = "FLAG = True\n"
    source = text.encode("utf-8")
    start = source.index(b"True")
    job = mutation.MutantJob(
        path="pkg/flags.py",
        original_text=text,
        site=mutation.MutationSite(
            start_byte=start,
            end_byte=start + len(b"True"),
            replacement=b"False",
            lineno=1,
            operator="python:bool-const-flip",
            description="True->False",
        ),
    )
    payload = _record(job, locus=False)
    fallback_fields = _locus_fields(_function_job())
    payload.update(fallback_fields)
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_JUDGE)

    assert isinstance(loaded, dict)
    assert "locus" not in loaded and "window_key" not in loaded


@pytest.mark.parametrize("defect", ["key", "schema", "window"])
def test_tampered_locus_resumes_and_is_counted(
    git_repo: GitRepo, tmp_path, defect: str
):
    _seed_function(git_repo)
    state = tmp_path / "state"
    code, _events = _run(git_repo, state, tmp_path / "1.jsonl")
    assert code == 0
    (path,) = sorted(state.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if defect == "window":
        payload["window_key"] = "f" * 64
    else:
        payload["locus"][defect] = "f" * 64 if defect == "key" else "assay-locus/2"
    path.write_text(json.dumps(payload), encoding="utf-8")

    code, events = _run(git_repo, state, tmp_path / "2.jsonl")

    assert code == 0
    assert _event(events, "candidate") is None
    resume = _event(events, "resume")
    assert resume["resumed_total"] == 1
    assert resume["rejected_total"] == 0
    assert resume["locus_unavailable_total"] == 1


def test_schema_one_is_read_by_resume_and_merge(git_repo: GitRepo, tmp_path):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _code, _events = _run(git_repo, state, tmp_path / "1.jsonl")
    (path,) = sorted(state.glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    carry = _carry_v1(record["judge_sha256"])
    record["carry_over"] = carry
    path.write_text(json.dumps(record), encoding="utf-8")

    _code, events = _run(git_repo, state, tmp_path / "2.jsonl")
    assert _event(events, "candidate") is None
    assert _event(events, "resume")["resumed_total"] == 1

    merged = tmp_path / "merged"
    _code, events = _run(
        git_repo, merged, tmp_path / "3.jsonl", "--merge-state", str(state)
    )
    assert _event(events, "carry_over")["copied_total"] == 1
    (copied,) = _records(merged)
    assert copied["carry_over"] == carry
    assert "provenance" not in copied


@pytest.mark.parametrize("kind", ["imported", "carried"])
def test_schema_two_is_valid_beside_matching_provenance(tmp_path, kind: str):
    job = _function_job()
    payload = _record(job)
    payload["carry_over"] = _carry_v2()
    payload["provenance"] = _provenance(mutation.candidate_id(job), kind=kind)
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(
        root, job, judge=_CURRENT_JUDGE
    )

    assert isinstance(loaded, dict) and loaded == payload
    assert mutation.record_provenance(loaded)["kind"] == kind


def test_schema_two_replay_shape_is_unusable(tmp_path):
    job = _function_job()
    payload = _record(job)
    payload["judge_sha256"] = _CURRENT_JUDGE
    payload["carry_over"] = _carry_v2()
    payload["provenance"] = _provenance(mutation.candidate_id(job))
    root = _store(tmp_path, job, payload)

    assert (
        mutation._load_validated_state_record(
            root, job, judge=_CURRENT_JUDGE
        )
        is mutation._RECORD_REJECTED
    )


def test_replayed_provenance_without_carry_over_resumes(
    git_repo: GitRepo, tmp_path
):
    _seed_function(git_repo)
    state = tmp_path / "state"
    _code, _events = _run(git_repo, state, tmp_path / "1.jsonl")
    (path,) = sorted(state.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provenance"] = _provenance(
        _OLD_JUDGE,
        kind="replayed",
        match="hint",
        from_judge_sha256=_OLD_JUDGE,
        replay_node_id="node-1",
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    _code, events = _run(git_repo, state, tmp_path / "2.jsonl")

    assert _event(events, "candidate") is None
    resume = _event(events, "resume")
    assert (resume["resumed_total"], resume["rejected_total"]) == (1, 0)


@pytest.mark.parametrize(
    ("carry_over", "provenance"),
    [
        ("not-a-mapping", _provenance()),
        (_carry_v2(), _provenance(kind="fresh")),
        (_carry_v2(), _provenance(kind=7)),
        (_carry_v2(), _provenance(match="guess")),
        (_carry_v2(), _provenance(match=7)),
        (_carry_v2(), _provenance(extra=True)),
        (_carry_v2(), {key: value for key, value in _provenance().items() if key != "match"}),
        (_carry_v2(), _provenance(from_candidate_id="not-an-id")),
        (_carry_v2(), _provenance(from_candidate_id=7)),
        (_carry_v2(), _provenance(from_candidate_id=_OLD_JUDGE)),
        (_carry_v2(), _provenance(from_judge_sha256=_OLD_JUDGE)),
        (_carry_v2(), _provenance(from_judge_sha256=7)),
        ({**_carry_v2(), "locus_match": "exact-id"}, _provenance()),
        (_carry_v2(schema_version=3), _provenance()),
        (_carry_v2(schema_version=True), _provenance()),
        (_carry_v2(into_judge_sha256=7), _provenance()),
        (_carry_v2(importer_version=None), _provenance()),
        (_carry_v2(), _ABSENT),
        (_carry_v2(), None),
        (
            _carry_v2(),
            {key: value for key, value in _provenance().items() if key != "from_candidate_id"},
        ),
        (_carry_v2(), _provenance(reason=7)),
        (_carry_v2(), _provenance(reason="")),
        (_carry_v2(), _provenance(reason="\ud800")),
        (_carry_v2(), _provenance(reason="x" * 1025)),
        (_ABSENT, _provenance()),
        (_ABSENT, _provenance(kind="carried")),
        (_ABSENT, _provenance(kind="replayed", replay_node_id=7)),
        (_ABSENT, _provenance(kind="replayed", replay_node_id="")),
        (_carry_v2(), _provenance(kind="imported", replay_node_id="node")),
        (_carry_v2(), _provenance(kind="replayed")),
    ],
)
def test_unusable_provenance_is_rejected_without_raising(
    tmp_path, carry_over, provenance
):
    job = _function_job()
    payload = _record(job)
    if carry_over is not _ABSENT:
        payload["carry_over"] = carry_over
    if isinstance(provenance, dict) and provenance.get("from_candidate_id") == _JUDGE:
        # bind the helper's placeholder to the job, so each row fails on its own defect
        provenance = {**provenance, "from_candidate_id": mutation.candidate_id(job)}
    if provenance is not _ABSENT:
        payload["provenance"] = provenance
    root = _store(tmp_path, job, payload)

    assert (
        mutation._load_validated_state_record(
            root, job, judge=_CURRENT_JUDGE
        )
        is mutation._RECORD_REJECTED
    )


def test_valid_imported_provenance_pair_is_accepted(tmp_path):
    """Positive control for the rejection rows above: the same record with a
    valid schema-2 carry_over and a matching provenance resumes."""
    job = _function_job()
    payload = _record(job)
    payload["carry_over"] = _carry_v2()
    payload["provenance"] = _provenance(mutation.candidate_id(job))
    root = _store(tmp_path, job, payload)

    loaded = mutation._load_validated_state_record(root, job, judge=_CURRENT_JUDGE)
    assert loaded is not None and loaded is not mutation._RECORD_REJECTED


@pytest.mark.parametrize("case", ["unknown-kind", "without-provenance"])
def test_invalid_provenance_is_reexecuted_and_counted(
    git_repo: GitRepo, tmp_path, case: str
):
    _seed_function(git_repo)
    state = tmp_path / "state"
    _code, _events = _run(git_repo, state, tmp_path / "1.jsonl")
    (path,) = sorted(state.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = {
        "kind": "imported",
        "from_candidate_id": payload["candidate_id"],
        "from_judge_sha256": payload["judge_sha256"],
        "match": "exact-id",
    }
    if case == "unknown-kind":
        payload["provenance"] = {**provenance, "kind": "fresh"}
    else:
        payload["carry_over"] = _carry_v2(
            into_judge_sha256=payload["judge_sha256"]
        )
    path.write_text(json.dumps(payload), encoding="utf-8")

    _code, events = _run(git_repo, state, tmp_path / "2.jsonl")

    assert _event(events, "candidate") is not None
    resume = _event(events, "resume")
    assert (resume["resumed_total"], resume["rejected_total"]) == (0, 1)


def test_carry_source_drops_bad_locus_counts_it_and_uses_exact_id(
    git_repo: GitRepo, tmp_path
):
    _seed_function(git_repo)
    source = tmp_path / "source"
    _code, _events = _run(git_repo, source, tmp_path / "1.jsonl")
    (path,) = sorted(source.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["locus"]["schema"] = "assay-locus/2"
    path.write_text(json.dumps(payload), encoding="utf-8")
    git_repo.write("tests/judge.sh", _BLIND + "# changed\n")
    git_repo.commit_all("judge change")

    target = tmp_path / "target"
    _code, events = _run(
        git_repo,
        target,
        tmp_path / "2.jsonl",
        "--carry-over",
        str(source),
        "--carry-over-reason",
        "exact id",
    )

    assert _event(events, "carry_over")["imported_total"] == 1
    assert _event(events, "resume")["locus_unavailable_total"] == 1
    (record,) = _records(target)
    assert "locus" not in record and "window_key" not in record


def test_locus_index_ignores_a_locus_claimed_by_two_candidate_ids(tmp_path):
    locus = _locus_fields(_function_job())["locus"]
    first = {"candidate_id": "a" * 64, "locus": locus, "window_key": "c" * 64}
    second = {"candidate_id": "b" * 64, "locus": dict(locus), "window_key": "d" * 64}
    store = tmp_path / "state"
    store.mkdir()
    for payload in (first, second):
        (store / f"{payload['candidate_id']}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    index = mutation.locus_index_for_records(_records(store))

    assert index.by_locus(locus["key"]) is None
    assert locus["key"] in index.ambiguous_loci()
    assert index.by_window("c" * 64) == (first,)
    assert index.by_window("d" * 64) == (second,)
    assert len(list(store.glob("*.json"))) == 2


def test_locus_index_deduplicates_one_candidate_before_detecting_collision():
    locus = _locus_fields(_function_job())["locus"]
    payload = {"candidate_id": "a" * 64, "locus": locus, "window_key": "c" * 64}
    archived_copy = {
        "candidate_id": "a" * 64,
        "locus": dict(locus),
        "window_key": "c" * 64,
        "generation": "archived",
    }

    index = mutation.locus_index_for_records([archived_copy, payload])

    assert index.by_locus(locus["key"]) == archived_copy
    assert index.ambiguous_loci() == frozenset()


def test_locus_index_skips_malformed_records_and_keys():
    locus = Locus(
        key="a" * 64,
        qualname="flags",
        segment_sha256="b" * 64,
        rel_start=1,
        rel_end=5,
    )
    fallback = {"candidate_id": "b" * 64, "locus": {}, "window_key": "e" * 64}
    keyed = {
        "candidate_id": "c" * 64,
        "locus": locus.to_record(),
        "window_key": "d" * 64,
    }
    bad_window = {
        "candidate_id": "e" * 64,
        "locus": {**locus.to_record(), "key": "f" * 64},
        "window_key": "not-a-digest",
    }
    malformed_candidate = {"candidate_id": "not-an-id", "locus": locus.to_record()}

    index = mutation.locus_index_for_records([
        None,
        malformed_candidate,
        fallback,
        keyed,
        bad_window,
    ])  # type: ignore[list-item]

    assert index.by_locus(locus.key) == keyed
    assert index.by_locus("f" * 64) == bad_window
    assert index.by_window("d" * 64) == (keyed,)
    assert index.by_window("e" * 64) == ()
    assert index.by_locus("not-a-digest") is None
    assert index.by_window("not-a-digest") == ()


def test_locus_fields_never_raises_and_omits_an_unavailable_window(monkeypatch):
    job = _function_job()
    source = job.original_text.encode("utf-8")
    real_locus_for_site = mutation._locus_for_site

    def explode(*_args, **_kwargs):
        raise RuntimeError("untrusted parser input")

    monkeypatch.setattr(mutation, "_locus_for_site", explode)
    assert (
        mutation._locus_fields(
            path=job.path,
            source=source,
            start_byte=job.site.start_byte,
            end_byte=job.site.end_byte,
            operator=job.site.operator,
            replacement_sha256=job.site.replacement_sha256,
        )
        == {}
    )

    monkeypatch.setattr(mutation, "_locus_for_site", real_locus_for_site)
    monkeypatch.setattr(mutation, "_window_key_for_site", lambda *_a, **_k: None)
    fields = _locus_fields(job)
    assert set(fields) == {"locus"}
