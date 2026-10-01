"""Targeted whole-source cases for CLI selection and path boundaries."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_lane, make_r2_judge
from assay import cli
from assay.config import LaneConfigError, MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.output import reserve_verdict_output


def _r2_lane(tmp_path, *, mutation=None):
    policy = mutation or MutationConfig(
        jobs=1,
        max_mutants=10,
        operators=("python:bool-const-flip",),
    )
    judge = make_r2_judge(source_root_paths=(tmp_path,), mutation=policy)
    return make_lane(rigor=("R0", "R2"), judge=judge)


def _plan_args(**overrides):
    values = {
        "file": "assay.toml",
        "lane": "package",
        "reuse_from": None,
        "shard": None,
        "operators": None,
        "request_base": None,
        "allow_dirty": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _patch_lane_file(monkeypatch, lane, tmp_path):
    lane_file = SimpleNamespace(project_root=tmp_path, lane=lambda _name: lane)
    monkeypatch.setattr(cli, "_resolve_lane_file", lambda _path: lane_file)


def test_argument_parser_usage_keeps_the_assay_headline():
    parser = cli.AssayArgumentParser(prog="assay")
    assert cli.cli_headline() in parser.format_usage()


def test_containments_checks_both_a_symlink_root_and_its_real_root(tmp_path):
    real_root = tmp_path / "real-project"
    real_root.mkdir()
    (real_root / "progress.jsonl").write_text("", encoding="utf-8")
    alias = tmp_path / "project-alias"
    alias.symlink_to(real_root, target_is_directory=True)
    target = alias / "progress.jsonl"

    pairs = cli._containments(target, alias)

    assert (alias, Path("progress.jsonl")) in pairs
    assert (real_root, Path("progress.jsonl")) in pairs


def test_existing_prefix_resolution_preserves_lexical_path_on_oserror(
    tmp_path, monkeypatch
):
    target = tmp_path / "progress.jsonl"
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == target:
            raise OSError("symlink loop")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert cli._resolve_through_existing_prefix(target) == target


def test_progress_heartbeat_rejects_nan_before_opening_destination():
    args = argparse.Namespace(progress="progress.jsonl", progress_heartbeat="nan")
    with pytest.raises(LaneConfigError, match="must be a finite number"):
        cli._resolve_progress_heartbeat(args)


@pytest.mark.parametrize(
    ("progress", "heartbeat", "expected"),
    [
        (None, "not-a-number", None),
        ("progress.jsonl", None, cli.runner.PROGRESS_HEARTBEAT_DEFAULT_SECONDS),
        ("progress.jsonl", "30", 30.0),
    ],
)
def test_progress_heartbeat_derives_absence_and_accepts_valid_values(
    progress, heartbeat, expected
):
    args = argparse.Namespace(progress=progress, progress_heartbeat=heartbeat)
    assert cli._resolve_progress_heartbeat(args) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("invalid", "must be a number"),
        ("0.01", "is below the"),
    ],
)
def test_progress_heartbeat_refuses_malformed_and_sub_floor_values(raw, message):
    args = argparse.Namespace(progress="progress.jsonl", progress_heartbeat=raw)
    with pytest.raises(LaneConfigError, match=message):
        cli._resolve_progress_heartbeat(args)


def test_visible_store_refuses_pathspec_magic_before_asking_git(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli.git,
        "path_is_ignored",
        lambda *_args, **_kwargs: pytest.fail("git must not receive pathspec magic"),
    )
    with pytest.raises(LaneConfigError, match="beginning with ':'"):
        cli._refuse_a_visible_store_inside_the_tree(
            ":state",
            flag="--state-dir",
            what="resume state",
            root=tmp_path,
            probe=Path(":state"),
        )


def test_visible_store_accepts_a_gitignored_path_inside_the_project(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli.git, "path_is_ignored", lambda *_args, **_kwargs: True)
    cli._refuse_a_visible_store_inside_the_tree(
        ".assay/state",
        flag="--state-dir",
        what="resume state",
        root=tmp_path,
        probe=Path(".assay/state/candidate.json"),
    )


def test_state_directory_inside_a_visible_project_path_is_refused(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli.git, "path_is_ignored", lambda *_args, **_kwargs: False)
    with pytest.raises(LaneConfigError, match="--state-dir.*not git-ignored"):
        cli._resolve_state_dir(
            argparse.Namespace(state_dir=str(tmp_path / "state")), tmp_path
        )


def test_progress_file_inside_a_visible_project_path_is_refused(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli.git, "path_is_ignored", lambda *_args, **_kwargs: False)
    with pytest.raises(LaneConfigError, match="--progress.*not git-ignored"):
        cli._refuse_a_visible_progress_destination(
            str(tmp_path / "progress.jsonl"), tmp_path
        )


def test_symlink_destination_probe_accepts_a_lexical_directory(tmp_path):
    cli._refuse_a_destination_reached_through_a_symlink(
        "state.json",
        flag="--verdict-json",
        what="the verdict",
        root=tmp_path,
        probe=Path("state.json"),
    )


def _lane_timeout(message="lane budget expired"):
    return AssayError(
        message,
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )


def test_pre_head_timeout_writes_a_complete_refusal_when_label_is_readable(
    tmp_path, monkeypatch
):
    lane = make_lane()
    lane_file = SimpleNamespace(project_root=tmp_path)
    first_timeout = _lane_timeout()
    calls = []

    def head_rev(*_args, **_kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise first_timeout
        return "a" * 40

    monkeypatch.setattr(cli.git, "head_rev", head_rev)
    output_path = tmp_path / "verdict.json"
    out, err = io.StringIO(), io.StringIO()
    destination = reserve_verdict_output(str(output_path), stdout=out)
    try:
        exit_code = cli._run_reserved(
            argparse.Namespace(
                verdict_json=str(output_path), require_judge_provenance=False
            ),
            lane,
            lane_file,
            [],
            destination,
            out,
            err,
        )
    finally:
        destination.close()

    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 4
    assert document["commit"] == "a" * 40
    assert (document["outcome"], document["reason_code"]) == (
        "BUDGET_EXCEEDED",
        "LANE_TIMEOUT",
    )
    assert "package: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)" in out.getvalue()
    assert len(calls) == 2


def test_pre_head_timeout_without_a_verdict_file_still_reports_the_terminal(
    tmp_path, monkeypatch
):
    calls = []

    def head_rev(*_args, **_kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise _lane_timeout()
        return "b" * 40

    monkeypatch.setattr(cli.git, "head_rev", head_rev)
    out = io.StringIO()
    exit_code = cli._run_reserved(
        argparse.Namespace(verdict_json=None, require_judge_provenance=False),
        make_lane(),
        SimpleNamespace(project_root=tmp_path, snapshot_limits=None, dirty_ignore=()),
        [],
        None,
        out,
        io.StringIO(),
    )

    assert exit_code == 4
    assert "package: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)" in out.getvalue()
    assert len(calls) == 2


def test_pre_head_timeout_can_emit_json_directly_to_stdout(tmp_path, monkeypatch):
    first_timeout = _lane_timeout()
    calls = []

    def head_rev(*_args, **_kwargs):
        calls.append(None)
        if len(calls) == 1:
            raise first_timeout
        return "c" * 40

    monkeypatch.setattr(cli.git, "head_rev", head_rev)
    out = io.StringIO()
    destination = reserve_verdict_output("-", stdout=out)
    try:
        exit_code = cli._run_reserved(
            argparse.Namespace(verdict_json="-", require_judge_provenance=False),
            make_lane(),
            SimpleNamespace(project_root=tmp_path),
            [],
            destination,
            out,
            io.StringIO(),
        )
    finally:
        destination.close()

    document = json.loads(out.getvalue())
    assert exit_code == 4
    assert document["commit"] == "c" * 40


def test_pre_head_timeout_refuses_when_its_bounded_label_read_times_out(
    tmp_path, monkeypatch
):
    first_timeout = _lane_timeout()
    calls = []

    def head_rev(*_args, **_kwargs):
        calls.append(None)
        raise first_timeout if len(calls) == 1 else _lane_timeout("label timeout")

    monkeypatch.setattr(cli.git, "head_rev", head_rev)
    output_path = tmp_path / "verdict.json"
    destination = reserve_verdict_output(str(output_path), stdout=io.StringIO())
    try:
        with pytest.raises(AssayError, match="commit label.*could not be read") as caught:
            cli._run_reserved(
                argparse.Namespace(
                    verdict_json=str(output_path), require_judge_provenance=False
                ),
                make_lane(),
                SimpleNamespace(project_root=tmp_path),
                [],
                destination,
                io.StringIO(),
                io.StringIO(),
                label_grace_seconds=0.0,
            )
    finally:
        destination.close()

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert not output_path.exists()


def test_pre_head_timeout_preserves_a_non_timeout_label_git_error(
    tmp_path, monkeypatch
):
    first_timeout = _lane_timeout()
    label_error = AssayError(
        "git is unavailable during label read",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )
    calls = []

    def head_rev(*_args, **_kwargs):
        calls.append(None)
        raise first_timeout if len(calls) == 1 else label_error

    monkeypatch.setattr(cli.git, "head_rev", head_rev)
    with pytest.raises(AssayError) as caught:
        cli._run_reserved(
            argparse.Namespace(verdict_json=None, require_judge_provenance=False),
            make_lane(),
            SimpleNamespace(project_root=tmp_path),
            [],
            None,
            io.StringIO(),
            io.StringIO(),
        )

    assert caught.value is first_timeout
    assert len(calls) == 2


def test_run_lane_timeout_is_rendered_as_a_complete_refusal(tmp_path, monkeypatch):
    timeout = _lane_timeout("lane run expired")
    monkeypatch.setattr(cli.git, "head_rev", lambda *_a, **_k: "d" * 40)
    monkeypatch.setattr(
        cli.runner,
        "run_lane",
        lambda *_a, **_k: (_ for _ in ()).throw(timeout),
    )
    out = io.StringIO()

    exit_code = cli._run_reserved(
        argparse.Namespace(verdict_json=None, require_judge_provenance=False),
        make_lane(),
        SimpleNamespace(project_root=tmp_path, snapshot_limits=None, dirty_ignore=()),
        [],
        None,
        out,
        io.StringIO(),
    )

    assert exit_code == 4
    assert "package: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)" in out.getvalue()


def test_run_applies_a_valid_operator_override_to_the_resolved_lane(
    tmp_path, monkeypatch
):
    lane = _r2_lane(tmp_path)
    _patch_lane_file(monkeypatch, lane, tmp_path)
    observed = {}

    def run_reserved(args, resolved_lane, *_args, **_kwargs):
        observed["lane"] = resolved_lane
        return 0

    monkeypatch.setattr(cli, "_run_reserved", run_reserved)
    args = argparse.Namespace(
        file="assay.toml",
        lane="package",
        operators="python:compare-swap",
        verdict_json=None,
        progress=None,
        progress_heartbeat=None,
        state_dir=None,
    )

    assert cli._cmd_run(args, [], io.StringIO(), io.StringIO()) == 0
    assert observed["lane"].judge.mutation.operators == ("python:compare-swap",)
    assert lane.judge.mutation.operators == ("python:bool-const-flip",)


def test_plan_refuses_a_lane_without_r2_before_resolving_an_adapter(
    tmp_path, monkeypatch
):
    lane = make_lane()
    _patch_lane_file(monkeypatch, lane, tmp_path)
    with pytest.raises(LaneConfigError, match="does not declare an R2 mutation judge"):
        cli._cmd_plan(_plan_args(), io.StringIO())


def test_plan_refuses_reuse_with_a_shard_before_loading_evidence(tmp_path, monkeypatch):
    lane = _r2_lane(tmp_path)
    _patch_lane_file(monkeypatch, lane, tmp_path)
    with pytest.raises(LaneConfigError, match="cannot be combined with --shard"):
        cli._cmd_plan(
            _plan_args(reuse_from=tmp_path / "prior.json", shard="0/2"),
            io.StringIO(),
        )


def test_plan_refuses_reuse_for_an_ingested_mutation_lane(tmp_path, monkeypatch):
    lane = _r2_lane(
        tmp_path,
        mutation=MutationConfig(
            format="stryker-json",
            artifact="mutation-report.json",
            fail_under=90.0,
        ),
    )
    _patch_lane_file(monkeypatch, lane, tmp_path)
    with pytest.raises(LaneConfigError, match="requires a native R2 mutation lane"):
        cli._cmd_plan(
            _plan_args(reuse_from=tmp_path / "prior.json"), io.StringIO()
        )


def test_plan_refuses_when_no_declared_mutation_adapter_resolves(tmp_path, monkeypatch):
    lane = _r2_lane(tmp_path)
    _patch_lane_file(monkeypatch, lane, tmp_path)
    monkeypatch.setattr(cli, "_resolve_declared_adapters", lambda _lane: None)
    with pytest.raises(LaneConfigError, match="resolves no mutation adapter"):
        cli._cmd_plan(_plan_args(), io.StringIO())
