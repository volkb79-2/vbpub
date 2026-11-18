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

_JUDGE = "j" * 64
_OLD_JUDGE = "a" * 64
_CURRENT_JUDGE = "b" * 64
_UNRELATED_JUDGE = "c" * 64
_FUNCTION_SOURCE = "def flags():\n    return True\n"


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


def test_new_candidate_records_and_progress_events_carry_locus_and_window(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    _seed_function(git_repo)
    real_fields_for_job = mutation._locus_fields_for_job
    calls = 0

    def counted_fields_for_job(job):
        nonlocal calls
        calls += 1
        return real_fields_for_job(job)

    monkeypatch.setattr(mutation, "_locus_fields_for_job", counted_fields_for_job)
    code, events = _run(git_repo, tmp_path / "state", tmp_path / "progress.jsonl")
    assert code == 0
    assert calls == 1
    candidate = _event(events, "candidate")
    assert candidate is not None
    assert candidate["event"] in mutation.PROGRESS_EVENTS
    (record,) = _records(tmp_path / "state")
    fields = _locus_fields(_function_job())
    assert fields.keys() == {"locus", "window_key"}
    assert candidate["locus"] == record["locus"] == fields["locus"]
    assert candidate["window_key"] == record["window_key"] == fields["window_key"]
    assert candidate["locus"]["qualname"] == "flags"


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


def test_record_locus_must_bind_the_jobs_span_line_and_operator(tmp_path):
    job, other = _first_and_second_jobs()
    payload = _record(job)
    swapped = _record(other)
    for key in (
        "start_byte",
        "end_byte",
        "lineno",
        "description",
        "locus",
        "window_key",
    ):
        payload[key] = swapped[key]
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


def test_record_locus_rejects_a_span_swapped_without_its_locus(tmp_path):
    job, other = _first_and_second_jobs()
    payload = _record(job)
    payload["start_byte"] = other.site.start_byte
    payload["end_byte"] = other.site.end_byte
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


def test_record_locus_rejects_an_operator_that_does_not_bind_the_job(tmp_path):
    job, _other = _first_and_second_jobs()
    payload = _record(job)
    payload["operator"] = "python:compare-swap"
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


def test_tampered_locus_is_stale_corruption(tmp_path):
    job = _function_job()
    payload = _record(job)
    payload["locus"] = {**payload["locus"], "key": "f" * 64}
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
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
def test_present_locus_with_wrong_shape_is_stale_corruption(tmp_path, bad_locus):
    job = _function_job()
    payload = _record(job)
    payload["locus"] = bad_locus
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


def test_window_key_must_match_locus(tmp_path):
    job = _function_job()
    payload = _record(job)
    payload["window_key"] = "f" * 64
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


def test_window_key_without_locus_is_stale_corruption(tmp_path):
    job = _function_job()
    payload = _record(job, locus=False)
    payload["window_key"] = "f" * 64
    root = _store(tmp_path, job, payload)

    with pytest.raises(MutationStateError, match="stale locus"):
        mutation._load_validated_state_record(root, job, judge=_JUDGE)


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


def _carry_v2(**overrides):
    block = {
        "schema_version": mutation.CARRY_OVER_SCHEMA_VERSION_V2,
        "from_judge_sha256": _OLD_JUDGE,
        "into_judge_sha256": _CURRENT_JUDGE,
        "source": "/prior/store",
        "reason": mutation.CARRY_FORWARD_REASON,
        "imported_at": "2026-11-18T00:00:00Z",
        "importer_version": "12.3.0",
        "locus_match": "exact-id",
    }
    block.update(overrides)
    return block


def test_schema_two_carry_over_accepts_every_lookup_kind():
    for match in ("exact-id", "locus", "window"):
        block = _carry_v2(locus_match=match)
        assert mutation._valid_carry_over(block) is True
        assert (
            mutation.record_effective_judge(
                {"judge_sha256": _OLD_JUDGE, "carry_over": block}
            )
            == _CURRENT_JUDGE
        )


def test_schema_two_from_judge_is_provenance_not_an_override():
    block = _carry_v2()
    replayed = {"judge_sha256": _CURRENT_JUDGE, "carry_over": block}

    assert mutation.record_effective_judge(replayed) == _CURRENT_JUDGE
    assert (
        mutation.record_effective_judge(
            {"judge_sha256": _UNRELATED_JUDGE, "carry_over": block}
        )
        is None
    )


def test_schema_one_carry_over_keeps_its_executing_judge_rule():
    block = {
        "schema_version": mutation.CARRY_OVER_SCHEMA_VERSION,
        "from_judge_sha256": _OLD_JUDGE,
        "into_judge_sha256": _CURRENT_JUDGE,
        "source": "/prior/store",
        "reason": "operator checked the change",
        "imported_at": "2026-11-18T00:00:00Z",
        "importer_version": "12.2.0",
    }
    assert mutation._valid_carry_over(block) is True
    assert (
        mutation.record_effective_judge(
            {"judge_sha256": _OLD_JUDGE, "carry_over": block}
        )
        == _CURRENT_JUDGE
    )
    assert (
        mutation.record_effective_judge(
            {"judge_sha256": _CURRENT_JUDGE, "carry_over": block}
        )
        is None
    )


@pytest.mark.parametrize(
    "block",
    [
        _carry_v2(schema_version=3),
        _carry_v2(schema_version=True),
        _carry_v2(locus_match="guess"),
        _carry_v2(locus_match=[]),
        _carry_v2(from_judge_sha256="nope"),
        {**_carry_v2(), "extra": True},
        {key: value for key, value in _carry_v2().items() if key != "locus_match"},
    ],
)
def test_bad_schema_two_carry_over_is_rejected(block):
    assert mutation._valid_carry_over(block) is False
    assert (
        mutation.record_effective_judge(
            {"judge_sha256": _CURRENT_JUDGE, "carry_over": block}
        )
        is None
    )
