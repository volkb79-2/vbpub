"""B527/B529-B531 -- the refusal and degradation edges of flexible R2 campaigns.

``test_flex_r2_campaigns.py`` measures the features end to end. This module
pins what each guard does with input it must not trust: malformed projection
documents, malformed ``carry_over`` blocks, judge generations that are not
directories, records that are symlinks, oversized or non-UTF-8 candidate
lists, and the run/plan flag combinations that are refused.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import GitRepo
from test_flex_r2_campaigns import (
    _BLIND,
    _STRICT_AB,
    _event,
    _run,
    _seed,
    _stable_resource_counters,
)

from assay import _judge_projection, cli, mutation
from assay.cli import main
from assay.errors import LaneConfigError

#: The autouse fixture of the end-to-end module applies here too.
_AUTOUSE = (_stable_resource_counters,)

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64


# --------------------------------------------------------------------------
# B529 projection: documents of unexpected shape
# --------------------------------------------------------------------------


def test_toml_datetimes_are_projected_canonically_and_other_types_refused():
    raw = b"when = 2026-11-11\nat = 07:32:00\nstamp = 2026-11-11T07:32:00Z\n"
    first = _judge_projection.project_config("assay", raw)
    second = _judge_projection.project_config("assay", raw.replace(b"\n", b"\r\n"))
    assert first.digest == second.digest
    assert _judge_projection._json_default(_dt.date(2026, 11, 11)) == {
        "toml-datetime": "2026-11-11"
    }
    with pytest.raises(TypeError, match="unexpected TOML value type"):
        _judge_projection._json_default(object())


def test_non_table_lanes_and_lanes_without_resource_keys_remove_nothing():
    assert _judge_projection.project_lane_document({"lanes": "not-a-table"}) == {}
    assert _judge_projection.project_lane_document({"lanes": {"unit": {"argv": []}}}) == {}
    assert _judge_projection.project_run_gate_document({"lanes": 3}) == {}
    assert (
        _judge_projection.project_run_gate_document({"lanes": {"unit": {"kind": "assay"}}})
        == {}
    )


def test_run_gate_top_level_resource_tables_are_removed():
    document = {"profile": {"mode": "off"}, "schema_version": 1}
    removed = _judge_projection.project_run_gate_document(document)
    assert removed == {"top": {"profile": {"mode": "off"}}}
    assert document == {"schema_version": 1}


def test_unknown_projection_kind_is_refused():
    with pytest.raises(ValueError, match="unknown configuration kind"):
        _judge_projection.project_config("cmru", b"")


# --------------------------------------------------------------------------
# FlexOptions and the carry_over block
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"lane_file_name": "dir/assay.toml"}, "plain file name"),
        ({"lane_file_name": ".."}, "plain file name"),
        ({"lane_file_name": ""}, "plain file name"),
        ({"carry_over_reason": "why"}, "required together"),
        (
            {"carry_over_sources": (Path("/s"),), "carry_over_reason": "  "},
            "non-empty text",
        ),
        (
            {"carry_over_sources": (Path("/s"),), "carry_over_reason": "x" * 1025},
            "exceeds 1024 bytes",
        ),
        ({"only_candidates": {_A}}, "frozenset of candidate ids"),
        ({"exclude_candidates": frozenset({"not-an-id"})}, "frozenset of candidate ids"),
    ],
)
def test_flex_options_refuse_malformed_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        mutation.FlexOptions(**kwargs)


def test_flex_options_report_what_they_read_and_select():
    plain = mutation.FlexOptions()
    assert (plain.reads_other_stores, plain.selection_active) == (False, False)
    merge = mutation.FlexOptions(merge_sources=(Path("/m"),))
    assert merge.reads_other_stores is True
    only = mutation.FlexOptions(only_candidates=frozenset({_A}))
    assert only.selection_active is True


def _carry(**overrides):
    block = {
        "schema_version": 1,
        "from_judge_sha256": _A,
        "into_judge_sha256": _B,
        "source": "/s",
        "reason": "why",
        "imported_at": "2026-11-11T00:00:00Z",
        "importer_version": "12.2.0",
    }
    block.update(overrides)
    return block


@pytest.mark.parametrize(
    "block",
    [
        "not-a-mapping",
        {"schema_version": 1},
        _carry(schema_version=True),
        _carry(schema_version=2),
        _carry(from_judge_sha256="A" * 64),
        _carry(into_judge_sha256=7),
        _carry(reason=""),
        _carry(importer_version=None),
    ],
)
def test_a_malformed_carry_over_block_has_no_effective_judge(block):
    assert mutation._valid_carry_over(block) is False
    assert mutation.record_effective_judge({"judge_sha256": _A, "carry_over": block}) is None


def test_effective_judge_of_plain_imported_and_judgeless_records():
    assert mutation.record_effective_judge({"judge_sha256": _A}) == _A
    assert mutation.record_effective_judge({"judge_sha256": _A, "carry_over": _carry()}) == _B
    # The block must name the record's own executing judge.
    assert (
        mutation.record_effective_judge(
            {"judge_sha256": _C, "carry_over": _carry()}
        )
        is None
    )
    assert mutation.record_effective_judge({"judge_sha256": ""}) is None
    assert mutation.record_effective_judge({"judge_sha256": 5}) is None
    assert mutation.record_effective_judge({}) is None


# --------------------------------------------------------------------------
# B527 judge generations: directories that are missing, files, or links
# --------------------------------------------------------------------------


def _record(identity: str, judge: str, bucket: str = "killed", **extra) -> bytes:
    payload = {"candidate_id": identity, "judge_sha256": judge, "outcome_bucket": bucket}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def test_generation_directories_require_a_digest_and_may_be_absent(tmp_path: Path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match="SHA-256 digest"):
            mutation._open_generation_dir(fd, "j" * 64, create=False)
        assert mutation._open_generation_dir(fd, _A, create=False) is None
        created = mutation._open_generation_dir(fd, _A, create=True)
        assert created is not None
        os.close(created)
        again = mutation._open_generation_dir(fd, _A, create=True)
        assert again is not None
        os.close(again)
    finally:
        os.close(fd)
    assert (tmp_path / "judges" / _A).is_dir()


def test_reading_a_generation_tolerates_absence_and_non_directories(tmp_path: Path):
    identity = _C
    read = mutation._read_generation_record_bytes
    assert read(tmp_path / "missing", identity, _A, state_root_fd=None) is None
    assert read(tmp_path, identity, "not-a-digest", state_root_fd=None) is None
    assert read(tmp_path, identity, _A, state_root_fd=None) is None
    (tmp_path / "judges").write_text("a file, not a directory\n", encoding="utf-8")
    assert read(tmp_path, identity, _A, state_root_fd=None) is None


def _archive(tmp_path: Path, name: str, new_judge: str | None) -> bool:
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return mutation._archive_foreign_generation(fd, name, new_judge)
    finally:
        os.close(fd)


def test_archiving_skips_what_it_cannot_attribute_to_another_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert _archive(tmp_path, "absent.json", _B) is False
    (tmp_path / "dir.json").mkdir()
    assert _archive(tmp_path, "dir.json", _B) is False
    (tmp_path / "target.json").write_bytes(_record(_C, _A))
    (tmp_path / "link.json").symlink_to(tmp_path / "target.json")
    assert _archive(tmp_path, "link.json", _B) is False
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe")
    assert _archive(tmp_path, "binary.json", _B) is False
    (tmp_path / "list.json").write_bytes(b"[1]")
    assert _archive(tmp_path, "list.json", _B) is False
    (tmp_path / "fake-judge.json").write_bytes(_record(_C, "j" * 64))
    assert _archive(tmp_path, "fake-judge.json", _B) is False
    (tmp_path / "same.json").write_bytes(_record(_C, _B))
    assert _archive(tmp_path, "same.json", _B) is False
    monkeypatch.setattr(mutation, "MUTATION_STATE_RECORD_LIMIT", 4)
    (tmp_path / "big.json").write_bytes(_record(_C, _A))
    assert _archive(tmp_path, "big.json", _B) is False
    assert not (tmp_path / "judges").exists()


@pytest.mark.parametrize("temporary_already_gone", [False, True])
def test_a_failed_archive_rename_removes_its_temporary_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temporary_already_gone: bool
):
    (tmp_path / "rec.json").write_bytes(_record(_C, _A))
    real_unlink = os.unlink

    def failing_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if temporary_already_gone:
            real_unlink(src, dir_fd=src_dir_fd)
        raise OSError("replace failed")

    monkeypatch.setattr(mutation.os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace failed"):
        _archive(tmp_path, "rec.json", _B)
    assert os.listdir(tmp_path / "judges" / _A) == []
    assert (tmp_path / "rec.json").read_bytes() == _record(_C, _A)


def test_terminal_record_judges_skips_everything_it_cannot_read(tmp_path: Path):
    identity = _C
    name = mutation.mutation_state_record_name(identity)
    assert mutation.terminal_record_judges(tmp_path / "missing", identity) == ()
    (tmp_path / "judges").write_text("not a directory", encoding="utf-8")
    assert mutation.terminal_record_judges(tmp_path, identity) == ()
    (tmp_path / "judges").unlink()
    generations = tmp_path / "judges"
    for judge, body in (
        (_A, _record(identity, _A)),
        (_B, b"\xff not utf-8"),
        ("c" * 63 + "d", b"[]"),
        ("d" * 64, _record("e" * 64, "d" * 64)),
        ("e" * 64, _record(identity, "e" * 64, bucket=None)),
    ):
        (generations / judge).mkdir(parents=True)
        (generations / judge / name).write_bytes(body)
    (generations / "not-a-digest").mkdir()  # ignored
    (generations / ("1" * 64)).mkdir()
    (generations / ("1" * 64) / name).symlink_to(generations / _A / name)
    (tmp_path / name).write_bytes(_record(identity, _B, carry_over=_carry(into_judge_sha256="x")))
    assert mutation.terminal_record_judges(tmp_path, identity) == ((_A, "killed"),)


def test_carry_over_candidates_without_generations(tmp_path: Path):
    assert mutation._carry_over_candidates(tmp_path, "x.json") == ["x.json"]
    (tmp_path / "judges").write_text("file", encoding="utf-8")
    assert mutation._carry_over_candidates(tmp_path, "x.json") == ["x.json"]


# --------------------------------------------------------------------------
# B530 carry-over/merge sources with records it must refuse
# --------------------------------------------------------------------------


def test_carry_over_reports_unreadable_and_foreign_records_as_refused(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    good = tmp_path / "good"
    code, _ = _run(git_repo, good, tmp_path / "g.jsonl")
    [record_path] = sorted(good.glob("*.json"))
    source = tmp_path / "source"
    source.mkdir()
    # A symlinked record is unreadable through the descriptor walk.
    (source / record_path.name).symlink_to(record_path)
    # A record naming another candidate is a refused shape.
    foreign = json.loads(record_path.read_text(encoding="utf-8"))
    foreign["candidate_id"] = "f" * 64
    (source / "judges" / _A).mkdir(parents=True)
    (source / "judges" / _A / record_path.name).write_text(
        json.dumps(foreign), encoding="utf-8"
    )
    code, events = _run(
        git_repo,
        tmp_path / "fresh",
        tmp_path / "f.jsonl",
        "--carry-over",
        str(source),
        "--carry-over-reason",
        "refusal edges",
    )
    carry = _event(events, "carry_over")
    assert (carry["imported_total"], carry["copied_total"]) == (0, 0)
    assert carry["refused_total"] == 1
    reason = carry["refused_sample"][0]["reason"]
    assert "refused shape" in reason or "invalid in" in reason


def test_merge_state_re_executes_a_bucket_named_by_rejudge(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    shard = tmp_path / "shard"
    _run(git_repo, shard, tmp_path / "s.jsonl")
    code, events = _run(
        git_repo,
        tmp_path / "main",
        tmp_path / "m.jsonl",
        "--merge-state",
        str(shard),
        "--rejudge-outcome",
        "killed",
    )
    carry = _event(events, "carry_over")
    assert carry["copied_total"] == 0
    assert {item["reason"] for item in carry["refused_sample"]} == {
        "bucket killed is re-executed"
    }


def _deadline(git_repo: GitRepo, path: Path, campaign: str) -> Path:
    code = main(
        [
            "campaign", "init", "--campaign", campaign, "--lane", "unit",
            "--hours", "1", "--file", str(git_repo.path / "assay.toml"),
            "--out", str(path),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 0
    return path


def test_merge_state_refuses_records_of_another_campaign_deadline(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    first = _deadline(git_repo, tmp_path / "d1.json", "campaign-1")
    second = _deadline(git_repo, tmp_path / "d2.json", "campaign-2")
    shard = tmp_path / "shard"
    _run(git_repo, shard, tmp_path / "s.jsonl", "--campaign-deadline", str(first))
    code, events = _run(
        git_repo,
        tmp_path / "main",
        tmp_path / "m.jsonl",
        "--campaign-deadline",
        str(second),
        "--merge-state",
        str(shard),
    )
    carry = _event(events, "carry_over")
    assert carry["copied_total"] == 0
    assert carry["refused_sample"][0]["reason"].startswith("another judge in merge source")


def test_a_carried_over_record_is_rebound_to_the_running_campaign_deadline(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    old = tmp_path / "old"
    _run(git_repo, old, tmp_path / "o.jsonl")
    git_repo.write("tests/judge.sh", _BLIND + "# changed\n")
    git_repo.commit_all("judge change")
    deadline = _deadline(git_repo, tmp_path / "d.json", "campaign-1")
    new = tmp_path / "new"
    code, events = _run(
        git_repo,
        new,
        tmp_path / "n.jsonl",
        "--campaign-deadline",
        str(deadline),
        "--carry-over",
        str(old),
        "--carry-over-reason",
        "comment only",
    )
    assert _event(events, "carry_over")["imported_total"] == 1
    [record] = [json.loads(p.read_text(encoding="utf-8")) for p in new.glob("*.json")]
    assert record["carry_over"]["reason"] == "comment only"
    assert isinstance(record["campaign_deadline_sha256"], str)


def test_an_import_that_would_not_resume_is_refused(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _seed(git_repo, _BLIND)
    old = tmp_path / "old"
    _run(git_repo, old, tmp_path / "o.jsonl")
    git_repo.write("tests/judge.sh", _BLIND + "# changed\n")
    git_repo.commit_all("judge change")
    real = mutation._load_validated_state_record

    def reject_the_rebuilt_import(*args, **kwargs):
        raw = kwargs.get("raw_override", mutation._READ_FROM_STORE)
        if (
            isinstance(raw, bytes)
            and not kwargs.get("accept_any_judge")
            and b'"carry_over"' in raw
        ):
            return mutation._RECORD_REJECTED
        return real(*args, **kwargs)

    monkeypatch.setattr(mutation, "_load_validated_state_record", reject_the_rebuilt_import)
    code, events = _run(
        git_repo,
        tmp_path / "new",
        tmp_path / "n.jsonl",
        "--carry-over",
        str(old),
        "--carry-over-reason",
        "comment only",
    )
    carry = _event(events, "carry_over")
    assert carry["imported_total"] == 0
    assert carry["refused_sample"][0]["reason"] == "imported record would not resume"


def test_a_resumed_record_with_a_malformed_carry_over_block_re_executes(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _run(git_repo, state, tmp_path / "1.jsonl")
    [path] = sorted(state.glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["carry_over"] = {"schema_version": 1}
    path.write_text(json.dumps(payload), encoding="utf-8")
    _code, events = _run(git_repo, state, tmp_path / "2.jsonl")
    assert sum(1 for event in events if event.get("event") == "candidate") == 1


# --------------------------------------------------------------------------
# B531 selection and the run paths without a progress stream
# --------------------------------------------------------------------------


def _ids(git_repo: GitRepo, tmp_path: Path) -> list[str]:
    out = io.StringIO()
    main(
        [
            "plan",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--list-pending",
            "--state-dir",
            str(tmp_path / "empty"),
        ],
        stdout=out,
        stderr=None,
    )
    return out.getvalue().split()


def test_only_candidates_leaves_the_other_pending_candidate_unexecuted(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    ids = _ids(git_repo, tmp_path)
    assert len(ids) == 2
    only = tmp_path / "only.txt"
    only.write_text(ids[0] + "\n", encoding="utf-8")
    code, events = _run(
        git_repo, tmp_path / "s", tmp_path / "p.jsonl", "--only-candidates", str(only)
    )
    assert [e["candidate_id"] for e in events if e.get("event") == "candidate"] == [ids[0]]


def test_selection_and_carry_over_run_without_a_progress_stream(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _run(git_repo, state, tmp_path / "1.jsonl")
    ids = _ids(git_repo, tmp_path)
    only = tmp_path / "only.txt"
    only.write_text(ids[0] + "\n", encoding="utf-8")
    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(tmp_path / "other"),
            "--resume",
            "--carry-over",
            str(state),
            "--carry-over-reason",
            "no progress stream",
            "--only-candidates",
            str(only),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code in (0, 1, 4)
    assert len(list((tmp_path / "other").glob("*.json"))) == 1


def _run_mutation_validation(**overrides):
    """Call run_mutation with placeholders; the flex checks run before any of
    them is used."""
    kwargs = dict(
        baseline=None,
        prepared=None,
        plan=None,
        deadline=None,
        targets=(),
        adapter=None,
        jobs=1,
        max_mutants=1,
        operators=(),
        process_runner=None,
        clock=None,
    )
    kwargs.update(overrides)
    return mutation.run_mutation(**kwargs)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"flex": object()}, "must be FlexOptions"),
        (
            {"flex": mutation.FlexOptions(merge_sources=(Path("/m"),))},
            "requires resume=True",
        ),
        (
            {"flex": mutation.FlexOptions(only_candidates=frozenset({_A}))},
            "carry-over and candidate selection require",
        ),
    ],
)
def test_run_mutation_refuses_inconsistent_flex_requests(overrides, message):
    with pytest.raises(ValueError, match=message):
        _run_mutation_validation(**overrides)


def test_run_mutation_refuses_a_selection_beside_a_pilot(tmp_path: Path):
    with pytest.raises(ValueError, match="pilot candidate_selection"):
        _run_mutation_validation(
            flex=mutation.FlexOptions(only_candidates=frozenset({_A})),
            resume=True,
            state_root=tmp_path,
            candidate_selection=frozenset({_A}),
        )


# --------------------------------------------------------------------------
# CLI: candidate lists, flag combinations, --list-pending
# --------------------------------------------------------------------------


def test_candidate_lists_that_cannot_be_read_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with pytest.raises(LaneConfigError, match="cannot be read"):
        cli.parse_candidate_list(tmp_path / "missing.txt", flag="--only-candidates")
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(LaneConfigError, match="is not UTF-8"):
        cli.parse_candidate_list(binary, flag="--only-candidates")
    big = tmp_path / "big.txt"
    big.write_text(_A + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "CANDIDATE_LIST_LIMIT_BYTES", 10)
    with pytest.raises(LaneConfigError, match="exceeds 10 bytes"):
        cli.parse_candidate_list(big, flag="--exclude-candidates")


def _flex_args(tmp_path: Path, **values):
    base = dict(
        carry_over=None,
        merge_state=None,
        carry_over_reason=None,
        only_candidates=None,
        exclude_candidates=None,
        candidates_file=None,
        resume=True,
    )
    base.update(values)
    return SimpleNamespace(**base)


def test_flex_flags_require_a_native_r2_lane_and_no_pilot(tmp_path: Path):
    lane_file = SimpleNamespace(path=tmp_path / "assay.toml")
    only = tmp_path / "only.txt"
    only.write_text(_A + "\n", encoding="utf-8")
    with pytest.raises(LaneConfigError, match="native R2 mutation lane"):
        cli._resolve_flex_options(
            _flex_args(tmp_path, only_candidates=only), None, lane_file, native_r2=False
        )
    with pytest.raises(LaneConfigError, match="--candidates-file"):
        cli._resolve_flex_options(
            _flex_args(tmp_path, only_candidates=only, candidates_file=only),
            None,
            lane_file,
            native_r2=True,
        )
    assert (
        cli._resolve_flex_options(_flex_args(tmp_path), None, lane_file, native_r2=False)
        is None
    )


def test_flex_directories_must_exist_and_are_deduplicated(tmp_path: Path):
    lane_file = SimpleNamespace(path=tmp_path / "assay.toml")
    with pytest.raises(LaneConfigError, match="not an existing directory"):
        cli._resolve_flex_options(
            _flex_args(tmp_path, merge_state=[tmp_path / "missing"]),
            None,
            lane_file,
            native_r2=True,
        )
    flex = cli._resolve_flex_options(
        _flex_args(tmp_path, merge_state=[tmp_path, str(tmp_path)]),
        None,
        lane_file,
        native_r2=True,
    )
    assert flex.merge_sources == (tmp_path,)


def _plan(git_repo: GitRepo, *extra: str, stderr=None):
    out = io.StringIO()
    code = main(
        ["plan", "unit", "--file", str(git_repo.path / "assay.toml"), *extra],
        stdout=out,
        stderr=stderr,
    )
    return code, out.getvalue()


def test_list_pending_refusals(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    err = io.StringIO()
    code, _ = _plan(git_repo, "--list-pending", stderr=err)
    assert code != 0 and "--list-pending requires --state-dir" in err.getvalue()
    err = io.StringIO()
    code, _ = _plan(
        git_repo, "--list-pending", "--state-dir", str(tmp_path), "--json", stderr=err
    )
    assert code != 0 and "cannot be combined with --json" in err.getvalue()
    err = io.StringIO()
    code, _ = _plan(
        git_repo,
        "--list-pending",
        "--state-dir",
        str(tmp_path),
        "--judge",
        "Z" * 64,
        stderr=err,
    )
    assert code != 0 and "--judge must be" in err.getvalue()


def test_a_judgeless_source_record_is_a_refused_shape(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    old = tmp_path / "old"
    _run(git_repo, old, tmp_path / "o.jsonl")
    for path in old.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["judge_sha256"]
        path.write_text(json.dumps(payload), encoding="utf-8")
    code, events = _run(
        git_repo,
        tmp_path / "new",
        tmp_path / "n.jsonl",
        "--carry-over",
        str(old),
        "--carry-over-reason",
        "shape",
    )
    carry = _event(events, "carry_over")
    assert carry["imported_total"] == 0
    assert carry["refused_sample"][0]["reason"].startswith("refused shape in ")


def test_carry_over_never_imports_a_bucket_named_by_rejudge(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    old = tmp_path / "old"
    _run(git_repo, old, tmp_path / "o.jsonl")
    code, events = _run(
        git_repo,
        tmp_path / "new",
        tmp_path / "n.jsonl",
        "--carry-over",
        str(old),
        "--carry-over-reason",
        "rejudge wins",
        "--rejudge-outcome",
        "killed",
    )
    carry = _event(events, "carry_over")
    assert carry["imported_total"] == 0
    assert {item["reason"] for item in carry["refused_sample"]} == {
        "bucket killed is not carried over"
    }


def test_state_dir_and_judge_belong_to_list_pending(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    for extra in (["--state-dir", str(tmp_path)], ["--judge", _A]):
        err = io.StringIO()
        code, _ = _plan(git_repo, *extra, stderr=err)
        assert code != 0 and "apply only to --list-pending" in err.getvalue()


def test_list_pending_without_a_summary_stream(tmp_path: Path):
    out = io.StringIO()
    args = SimpleNamespace(judge=None, state_dir=tmp_path, shard=None)
    assert cli._print_pending(args, [], out, None, deadline_check=lambda: 0.0) == 0
    assert out.getvalue() == ""


def test_campaign_analysis_refuses_a_malformed_carry_over_block(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(git_repo.path)
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    progress = tmp_path / "p.jsonl"
    verdict = tmp_path / "v.json"
    _run(git_repo, state, progress, verdict=verdict)
    for path in state.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["carry_over"] = {"schema_version": 1}
        path.write_text(json.dumps(payload), encoding="utf-8")
    out = io.StringIO()
    main(
        [
            "analyze", "campaign", "unit", "--file", str(git_repo.path / "assay.toml"),
            "--expected-commit", git_repo.head(), "--progress", str(progress),
            "--state-dir", str(state), "--verdict", str(verdict), "--format", "json",
        ],
        stdout=out,
        stderr=io.StringIO(),
    )
    assert "carry_over provenance is malformed" in out.getvalue()


def test_a_candidate_absent_from_every_source_is_not_a_refusal(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    old = tmp_path / "old"
    _run(git_repo, old, tmp_path / "o.jsonl")
    sorted(old.glob("*.json"))[0].unlink()
    git_repo.write("tests/judge.sh", _STRICT_AB + "# changed\n")
    git_repo.commit_all("judge change")
    code, events = _run(
        git_repo,
        tmp_path / "new",
        tmp_path / "n.jsonl",
        "--carry-over",
        str(old),
        "--carry-over-reason",
        "one record missing",
    )
    carry = _event(events, "carry_over")
    assert (carry["imported_total"], carry["refused_total"]) == (1, 0)
    assert sum(1 for e in events if e.get("event") == "candidate") == 1
