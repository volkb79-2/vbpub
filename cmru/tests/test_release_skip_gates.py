"""CMRU-SKIP-01: ``cmru release --skip-gates REASON``, an explicit, recorded ungated release.

It skips only the execution of the declared ``run-tests`` step. The reason is
announced, shown in the plan and ``--dry-run``, recorded in the transaction, and
published in every Release body. A resume uses the recorded choice and can
neither add nor remove a skip.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import cmru.changelog  # noqa: F401  (import before ``child`` patches ``version.detect_changed_projects``,
# which ``cmru.changelog`` would otherwise bind for the rest of the session)
from cmru import cli, exit_codes, transaction, version
from cmru.release import (
    build_oci_image_release_body, build_release_body, build_variants_release_body,
)
from cmru.release_notes import GATES_SKIPPED_ENV, ReleaseNotes
from cmru.errors import RefusedBeforeChange
from cmru.version import ReleasePlanRefused

BASE = "a" * 40
REASON = "operator: re-publish after release cleanup"
LINE = f"Released without gates (operator): {REASON}"


def _workspace(tmp_path: Path, base: str = BASE) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-20261018_120000-demo-abcdef", base,
    )


@pytest.fixture
def scope(tmp_path, monkeypatch):
    directory = tmp_path / "scope"
    directory.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: directory)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: directory)
    return directory


def _record_path(scope_dir: Path, workspace) -> Path:
    return scope_dir / f"{transaction._release_token(workspace)}.gates.json"


# --- the option itself --------------------------------------------------------


@pytest.mark.parametrize("reason", ["", "   ", "\t \n"])
def test_an_empty_or_whitespace_reason_is_a_usage_error(reason, capsys):
    assert cli.main(["release", "--skip-gates", reason]) == 2
    assert "the reason must not be empty" in capsys.readouterr().err


def test_the_reason_is_collapsed_to_one_line():
    assert cli._gates_reason("  a\n  b\tc ") == "a b c"


def test_resume_with_skip_gates_is_refused_before_anything_else(capsys):
    assert cli.main(["release", "--resume", "/nonexistent", "--skip-gates", REASON]) == 2
    err = capsys.readouterr().err
    assert "--skip-gates cannot be combined with --resume" in err
    assert "can neither add nor remove a skip" in err


# --- what is skipped ----------------------------------------------------------


def _gate_project(steps):
    return cli.ProjectConfig("demo", {}, steps, prefix="demo-v", git_tag=False)


def test_skip_gates_does_not_execute_run_tests_and_runs_every_other_step(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    project = _gate_project({"run-tests": [object()]})
    workspace = _workspace(tmp_path)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *a: calls.append("progress"))
    monkeypatch.setattr(transaction, "start_untagged_publication_record", lambda *a: None)
    monkeypatch.setattr(transaction, "mark_untagged_publication_started", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: calls.append("prepare"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *a, **k: calls.append("backup"))
    monkeypatch.setattr(transaction, "promote_workspace", lambda *a, **k: calls.append("promote"))
    monkeypatch.setattr(transaction, "write_release_result", lambda *a: calls.append("result"))
    monkeypatch.setattr(cli, "_run_untagged_project", lambda *a, **k: calls.append("build+push"))
    monkeypatch.setattr(cli, "_assert_release_candidate_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_assert_release_gate_source_matches_commit",
        lambda *_a, phase, **_k: calls.append(f"source-check-{phase}"),
    )
    monkeypatch.setattr(cli, "_git", lambda *a, **k: "b" * 40)
    monkeypatch.setattr(
        cli, "run_project_step", lambda p, step, *a, **k: calls.append(f"step:{step}"),
    )

    def release(gates_skipped):
        calls.clear()
        cli._release_projects_sequentially(
            tmp_path, {"demo": project}, workspace, ["demo"],
            github_config=cli.GitHubConfig("o", "r", "t", "user"),
            env_config=cli.ReleaseEnvConfig({}, None), gates_skipped=gates_skipped,
        )
        return list(calls)

    gated = release(None)
    skipped = release(REASON)

    assert "step:run-tests" in gated
    assert "step:run-tests" not in skipped
    # Everything else is identical, including the source-consistency checks around the gate.
    assert [c for c in gated if c != "step:run-tests"] == skipped
    assert {"prepare", "backup", "source-check-before", "source-check-after",
            "build+push", "promote"} <= set(skipped)


def test_skip_gates_still_refuses_a_project_with_no_declared_gate(tmp_path):
    with pytest.raises(RuntimeError, match="no release gate is declared"):
        cli._run_release_gates(tmp_path, {"demo": _gate_project({})}, ["demo"], skip_gates=REASON)


def test_a_skipped_gate_never_asks_git_for_a_gate_commit(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "_git", lambda *a, **k: (_ for _ in ()).throw(AssertionError("git")),
    )
    monkeypatch.setattr(
        cli, "run_project_step", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gate")),
    )
    cli._run_release_gates(
        tmp_path, {"demo": _gate_project({"run-tests": [object()]})}, ["demo"], skip_gates=REASON,
    )
    assert "demo: release gate skipped by operator" in capsys.readouterr().out


def test_a_versioning_commit_rechecks_instead_of_rerunning_a_skipped_gate(monkeypatch, tmp_path, capsys):
    """The file strategy moves HEAD after the first gate; a skipped gate stays skipped."""
    seen = []
    receipts = iter(("gated", "candidate"))

    def exact_head(*args, **kwargs):
        seen.append(kwargs.get("skip_gates"))
        return next(receipts)

    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(cli.transaction, "write_release_progress", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "start_untagged_publication_record", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "mark_untagged_publication_started", lambda *a: None)
    monkeypatch.setattr(cli.transaction, "push_backup_branch", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "promote_workspace", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "write_release_result", lambda *a, **k: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_release_gates_on_exact_head", exact_head)
    monkeypatch.setattr(cli, "_git", lambda *a, **k: "candidate")
    monkeypatch.setattr(version, "release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *a: "demo-v1.0.0")
    monkeypatch.setattr(cli, "_push_tags", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_assert_release_candidate_unchanged", lambda *a, **k: None)
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v")
    cli._release_projects_sequentially(
        tmp_path, {"demo": project}, _workspace(tmp_path), ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True, gates_skipped=REASON,
    )
    assert seen == [REASON, REASON]
    assert "re-checking the candidate source (release gate skipped by operator)" in capsys.readouterr().out


# --- the plan, the warning, dry-run and the transaction's record --------------


def _child_config(tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


@pytest.fixture
def child(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _child_config(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *a, **k: [("demo", "changed")])
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, BASE)
    monkeypatch.setattr(transaction, "write_release_scope", lambda *a: calls.append("scope"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *a, **k: calls.append("backup"))
    monkeypatch.setattr(transaction, "mark_plan_refused", lambda *a: calls.append("refused"))
    monkeypatch.setattr(
        cli, "_release_projects_sequentially",
        lambda *a, **k: calls.append(("sequential", k["gates_skipped"])) or [],
    )
    monkeypatch.setattr(
        version, "release_cmd", lambda *a, **k: calls.append(("release_cmd", a[1], k)),
    )
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root, **_kwargs: True)
    return SimpleNamespace(calls=calls, config=str(tmp_path / "cmru.toml"))


def test_the_plan_warns_once_and_shows_run_tests_as_skipped(child, capsys):
    cli.main(["release", "--config", child.config, "--skip-gates", REASON])

    out = capsys.readouterr().out
    assert out.count(f"[WARN] release gates skipped by operator: {REASON}") == 1
    assert f"demo: phase run-tests: skipped (operator: {REASON})" in out
    assert ("sequential", REASON) in child.calls
    assert os.environ[GATES_SKIPPED_ENV] == REASON


def test_dry_run_shows_the_skip_and_publishes_nothing(child, capsys):
    cli.main(["release", "--config", child.config, "--dry-run", "--skip-gates", REASON])

    out = capsys.readouterr().out
    assert f"[WARN] release gates skipped by operator: {REASON}" in out
    assert f"demo: phase run-tests: skipped (operator: {REASON})" in out
    assert "[DRY RUN] No tags pushed" in out
    assert not any(call == "scope" or call == "backup" for call in child.calls)


def test_a_gated_release_prints_nothing_about_skipping_and_clears_a_stale_value(
    child, monkeypatch, capsys,
):
    monkeypatch.setenv(GATES_SKIPPED_ENV, "stale shell value")

    cli.main(["release", "--config", child.config])

    assert "skipped" not in capsys.readouterr().out
    assert ("sequential", None) in child.calls
    assert GATES_SKIPPED_ENV not in os.environ


@pytest.mark.real_release_gates_plan
def test_the_evidence_records_the_skip_and_a_resume_uses_it(child, scope, tmp_path, capsys):
    cli.main(["release", "--config", child.config, "--skip-gates", REASON])
    (record,) = scope.glob("*.gates.json")
    document = json.loads(record.read_text(encoding="utf-8"))
    assert document["gates"] == {"skipped": True, "reason": REASON}
    assert document["transaction"] == "cmru/release/child"

    # The resume: the launcher drops --resume and the child gets no --skip-gates.
    capsys.readouterr()
    child.calls.clear()
    os.environ.pop(GATES_SKIPPED_ENV)
    cli.main(["release", "--config", child.config])

    out = capsys.readouterr().out
    assert f"[WARN] release gates skipped by operator: {REASON}" in out
    assert ("sequential", REASON) in child.calls
    assert os.environ[GATES_SKIPPED_ENV] == REASON


@pytest.mark.real_release_gates_plan
def test_a_resumed_gated_transaction_cannot_gain_a_skip(child, scope, capsys):
    cli.main(["release", "--config", child.config])
    capsys.readouterr()
    child.calls.clear()

    assert cli.main(
        ["release", "--config", child.config, "--skip-gates", REASON],
    ) == exit_codes.REFUSED

    assert child.calls == ["refused"]
    assert "recorded gated (no --skip-gates)" in capsys.readouterr().err


@pytest.mark.real_release_gates_plan
def test_a_resumed_skipped_transaction_cannot_change_its_reason(child, scope, capsys):
    cli.main(["release", "--config", child.config, "--skip-gates", REASON])
    capsys.readouterr()
    child.calls.clear()

    assert cli.main(
        ["release", "--config", child.config, "--skip-gates", "another reason"],
    ) == exit_codes.REFUSED

    assert child.calls == ["refused"]
    assert f"recorded --skip-gates {REASON!r}" in capsys.readouterr().err


@pytest.mark.real_release_gates_plan
def test_the_same_reason_again_is_accepted(tmp_path, scope):
    workspace = _workspace(tmp_path)
    assert cli._release_gates_plan(tmp_path, workspace, REASON, dry_run=False) == REASON
    assert cli._release_gates_plan(tmp_path, workspace, REASON, dry_run=False) == REASON
    assert cli._release_gates_plan(tmp_path, workspace, None, dry_run=False) == REASON


@pytest.mark.real_release_gates_plan
def test_a_dry_run_records_nothing(tmp_path, scope):
    workspace = _workspace(tmp_path)
    assert cli._release_gates_plan(tmp_path, workspace, REASON, dry_run=True) == REASON
    assert list(scope.iterdir()) == []


# --- the durable record -------------------------------------------------------


def test_the_gates_record_round_trips_bound_to_its_transaction_and_is_written_once(
    tmp_path, scope,
):
    workspace = _workspace(tmp_path)
    assert transaction.read_release_gates(tmp_path, workspace) is None

    transaction.write_release_gates(tmp_path, workspace, REASON)

    assert transaction.read_release_gates(tmp_path, workspace) == {
        "skipped": True, "reason": REASON,
    }
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.write_release_gates(tmp_path, workspace, None)
    assert transaction.read_release_gates(tmp_path, workspace)["skipped"] is True
    transaction.forget_release_scope(tmp_path, workspace)
    assert not _record_path(scope, workspace).exists()


def test_a_gated_record_is_distinct_from_no_record(tmp_path, scope):
    workspace = _workspace(tmp_path)
    transaction.write_release_gates(tmp_path, workspace, None)
    assert transaction.read_release_gates(tmp_path, workspace) == {
        "skipped": False, "reason": None,
    }


@pytest.mark.parametrize("reason", ["", "  ", "two\nlines"])
def test_the_gates_writer_refuses_an_empty_or_multiline_reason(tmp_path, scope, reason):
    with pytest.raises(RuntimeError, match="non-empty single-line reason"):
        transaction.write_release_gates(tmp_path, _workspace(tmp_path), reason)
    assert list(scope.iterdir()) == []


def test_the_gates_writer_removes_a_partial_file_after_a_write_failure(
    tmp_path, scope, monkeypatch,
):
    workspace = _workspace(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", fail)
    with pytest.raises(OSError, match="disk full"):
        transaction.write_release_gates(tmp_path, workspace, REASON)
    assert not _record_path(scope, workspace).exists()


def _good(workspace):
    return {
        "schema_version": 1, "transaction": workspace.branch, "base": workspace.base,
        "gates": {"skipped": True, "reason": REASON},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=2),
        lambda d: d.update(schema_version=True),
        lambda d: d.update(transaction="other"),
        lambda d: d.update(base="b" * 40),
        lambda d: d.update(extra=1),
        lambda d: d.update(gates="skipped"),
        lambda d: d["gates"].update(extra=1),
        lambda d: d["gates"].update(skipped="yes"),
        lambda d: d["gates"].update(skipped=False),
        lambda d: d["gates"].update(reason="   "),
        lambda d: d["gates"].update(reason=7),
    ],
)
def test_a_malformed_or_foreign_gates_record_is_an_error_never_a_default(
    tmp_path, scope, mutate,
):
    workspace = _workspace(tmp_path)
    document = _good(workspace)
    mutate(document)
    _record_path(scope, workspace).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed or does not match"):
        transaction.read_release_gates(tmp_path, workspace)


def test_an_unreadable_or_non_regular_gates_record_is_an_error(tmp_path, scope):
    workspace = _workspace(tmp_path)
    path = _record_path(scope, workspace)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read release gates record"):
        transaction.read_release_gates(tmp_path, workspace)
    path.unlink()
    path.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        transaction.read_release_gates(tmp_path, workspace)


def test_an_uninspectable_gates_record_is_an_error(tmp_path, scope, monkeypatch):
    real = Path.lstat

    def lstat(self, *args, **kwargs):
        if str(self).endswith(".gates.json"):
            raise PermissionError("denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(RuntimeError, match="cannot inspect release gates record"):
        transaction.read_release_gates(tmp_path, _workspace(tmp_path))


# --- the published Release body ----------------------------------------------


def _body_kinds():
    notes = ReleaseNotes(version="1.2.3", source="CHANGES.md", text="## demo-v1.2.3 - 2026-10-21\n\n- Fixed.")
    return {
        "single": lambda: build_release_body(
            "demo", "1.2.3", artifact_name="demo-1.2.3.whl", sha256="0" * 64, release_notes=notes,
        ),
        "variants": lambda: build_variants_release_body(
            "demo", "1.2.3", records=[{"asset": "a.tgz", "sha256": "1" * 64}],
            release_notes=notes,
        ),
        "oci": lambda: build_oci_image_release_body(
            "ghcr.io/o/demo", "1.2.3", release_notes=notes,
        ),
    }


@pytest.mark.parametrize("kind", ["single", "variants", "oci"])
def test_every_release_body_states_an_ungated_release(kind, monkeypatch):
    build = _body_kinds()[kind]
    monkeypatch.delenv(GATES_SKIPPED_ENV, raising=False)
    gated = build()
    monkeypatch.setenv(GATES_SKIPPED_ENV, REASON)
    ungated = build()

    assert "Released without gates" not in gated
    assert LINE in ungated.splitlines()
    # The curated notes still come last and the rest of the body is unchanged.
    assert ungated.endswith("- Fixed.")
    assert ungated.replace(f"\n\n{LINE}", "") == gated


def test_an_empty_ambient_value_adds_no_line(monkeypatch):
    monkeypatch.setenv(GATES_SKIPPED_ENV, "  ")
    assert "Released without gates" not in _body_kinds()["single"]()


def test_the_dry_run_preview_and_the_publisher_share_the_ungated_line(child, monkeypatch, capsys):
    """The preview calls the same builder as the publisher, with the env the child set."""
    cli.main(["release", "--config", child.config, "--skip-gates", REASON, "--dry-run"])
    assert LINE in _body_kinds()["single"]().splitlines()


# --- the protected input handed to the publisher steps ------------------------


_STEP_PROJECT = """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
token_source = "cmru"
[targets]
host = "github"
registry = []
[runtime]
kind = "none"
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
"""


@pytest.mark.parametrize(
    "in_transaction, ambient, expected",
    [(True, REASON, REASON), (True, None, None), (False, "forged", None)],
)
def test_only_a_transaction_child_hands_the_reason_to_a_step(
    tmp_path, monkeypatch, in_transaction, ambient, expected,
):
    import subprocess

    monkeypatch.setattr(os, "environ", os.environ.copy())
    root = tmp_path / "demo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )
    (root / "cmru.toml").write_text(_STEP_PROJECT, encoding="utf-8")
    if ambient is None:
        os.environ.pop(GATES_SKIPPED_ENV, None)
    else:
        os.environ[GATES_SKIPPED_ENV] = ambient
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda *_a, **_k: in_transaction)
    seen = []
    monkeypatch.setattr(
        "cmru.cli.execute_step", lambda step, root_, log_dir, **kw: seen.append(kw),
    )

    assert cli.main(["run", "--step", "build", "--config", str(root / "cmru.toml")]) == 0

    assert seen[0]["protected_env"][GATES_SKIPPED_ENV] == expected


def test_a_project_cannot_declare_the_reserved_name():
    from cmru import config

    assert config.is_reserved_internal_env(GATES_SKIPPED_ENV)


# --- review round: reason cap, ambient clearing, typed refusal, wording -------


def test_a_reason_over_500_characters_is_a_usage_error(capsys):
    assert cli._gates_reason("x" * 500) == "x" * 500
    assert cli.main(["release", "--skip-gates", "x" * 501]) == 2
    assert "at most 500 are allowed" in capsys.readouterr().err


def test_the_skip_log_line_names_the_reason(monkeypatch, tmp_path, capsys):
    cli._run_release_gates(
        tmp_path, {"demo": _gate_project({"run-tests": [object()]})}, ["demo"], skip_gates=REASON,
    )
    assert f"release gate skipped by operator (--skip-gates): {REASON}" in capsys.readouterr().out


def test_the_child_clears_a_callers_exported_value_before_any_prepare_step(
    child, monkeypatch,
):
    seen = []
    monkeypatch.setenv(GATES_SKIPPED_ENV, "exported in the caller's shell")
    monkeypatch.setattr(
        cli, "_prepare_dry_run_external_versions",
        lambda *a, **k: seen.append(os.environ.get(GATES_SKIPPED_ENV)),
    )

    cli.main(["release", "--config", child.config, "--dry-run"])

    assert seen == [None]


@pytest.mark.real_release_gates_plan
@pytest.mark.parametrize("damage", ["{not json", "foreign"])
def test_an_unusable_gates_record_is_a_typed_refusal_not_a_crash(
    child, scope, capsys, damage,
):
    workspace = transaction.ReleaseWorkspace(Path("."), Path("."), "cmru/release/child", BASE)
    path = _record_path(scope, workspace)
    if damage == "foreign":
        document = _good(_workspace(Path(".")))
        path.write_text(json.dumps(document), encoding="utf-8")
    else:
        path.write_text(damage, encoding="utf-8")

    assert cli.main(["release", "--config", child.config]) == exit_codes.REFUSED

    assert child.calls == ["refused"]
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "unusable gates record" in err


def test_a_skipped_candidate_mismatch_does_not_talk_about_a_gate_receipt(monkeypatch, tmp_path):
    receipts = iter(("gated", "elsewhere"))
    for name, value in (
        ("write_release_progress", lambda *a: None),
        ("start_untagged_publication_record", lambda *a: None),
        ("mark_untagged_publication_started", lambda *a: None),
        ("push_backup_branch", lambda *a, **k: None),
    ):
        monkeypatch.setattr(transaction, name, value)
    monkeypatch.setattr(transaction, "read_release_source", lambda *_a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_run_release_gates_on_exact_head", lambda *a, **k: next(receipts))
    monkeypatch.setattr(cli, "_git", lambda *a, **k: "candidate")
    monkeypatch.setattr(version, "release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *a: "demo-v1.0.0")
    monkeypatch.setattr(cli, "_discard_unpushed_release_tags", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="recorded source check .* is for elsewhere") as raised:
        cli._release_projects_sequentially(
            tmp_path, {"demo": cli.ProjectConfig("demo", {}, {}, prefix="demo-v")},
            _workspace(tmp_path), ["demo"],
            github_config=cli.GitHubConfig("o", "r", "t", "user"),
            env_config=cli.ReleaseEnvConfig({}, None), gates_skipped=REASON,
        )
    assert "gate receipt" not in str(raised.value)


# --- the post-tag body retry (cmru publish --release-tag) ---------------------

TAG = "img-v1.2.3"
TAG_COMMIT = "c" * 40


class _FakeReleases:
    body = None  # None = no Release published

    def __init__(self, *_args):
        pass

    def get_release_by_tag(self, tag):
        assert tag == TAG
        return None if type(self).body is None else {"body": type(self).body}


def _git(root, *args):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def retry(scope, monkeypatch, tmp_path):
    """A real repo with a real ANNOTATED tag, created as cmru creates release tags (``git tag -a``)."""
    from cmru import release

    _FakeReleases.body = None
    monkeypatch.setattr(release, "GitHubReleases", _FakeReleases)
    gh = cli.GitHubConfig("o", "r", "t", "user")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "release candidate")
    _git(root, "tag", "-a", TAG, "-m", f"Release {TAG}")
    oid = _git(root, "rev-parse", f"refs/tags/{TAG}")
    commit = _git(root, "rev-parse", f"refs/tags/{TAG}^{{commit}}")
    assert oid != commit  # the trap: an annotated tag's object is not its commit

    def fact(**explicit):
        return cli._retry_gates_fact(
            root, "img", TAG, gh,
            gates_skipped=explicit.get("gates_skipped"),
            gates_ran=explicit.get("gates_ran", False),
        )

    def record(token, *, skip, tag_oid=None, with_gates=True):
        workspace = transaction.ReleaseWorkspace(
            root, tmp_path / token, f"cmru-release-{token}", BASE,
        )
        # Exactly what ``_push_tags`` records: the tag REF's own object id.
        transaction.write_release_tag_attempts(
            root, workspace, {f"refs/tags/{TAG}": tag_oid or cli.local_git_tag_oid(root, TAG)},
        )
        if with_gates:
            transaction.write_release_gates(root, workspace, skip)
        return workspace

    def retag():
        """Reuse the tag NAME at the SAME commit: a different tag object."""
        _git(root, "tag", "-d", TAG)
        _git(root, "tag", "-a", TAG, "-m", "re-created")
        assert _git(root, "rev-parse", f"refs/tags/{TAG}") != oid
        assert _git(root, "rev-parse", f"refs/tags/{TAG}^{{commit}}") == commit

    return SimpleNamespace(fact=fact, record=record, scope=scope, oid=oid, retag=retag)


def test_source_1_a_retained_skipped_transaction_restates_the_skip(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    assert retry.fact() == REASON


def test_source_1_a_retained_gated_transaction_stays_gated(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=None)
    assert retry.fact() is None


def test_source_1_ignores_a_transaction_that_made_a_different_tag_object_at_the_same_commit(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    retry.retag()
    with pytest.raises(RefusedBeforeChange, match="cannot determine"):
        retry.fact()


def test_source_1_a_real_annotated_release_tag_is_matched_and_a_gates_ran_claim_is_refused(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    assert retry.fact() == REASON
    with pytest.raises(RefusedBeforeChange, match="contradicts"):
        retry.fact(gates_ran=True)


def test_source_1_agreeing_transactions_are_fine_and_disagreeing_ones_refuse(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    retry.record("20261021_110000-img-bbbbbb", skip=REASON)
    assert retry.fact() == REASON
    retry.record("20261021_120000-img-cccccc", skip=None)
    with pytest.raises(RefusedBeforeChange, match="disagree"):
        retry.fact()


def test_source_1_a_transaction_without_a_gates_record_is_undetermined_not_gated(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=None, with_gates=False)
    with pytest.raises(RefusedBeforeChange, match="--gates-ran"):
        retry.fact()


@pytest.mark.parametrize("damage", ["{not json", "[1]"])
def test_an_unrelated_unreadable_attempt_record_is_skipped_with_a_warning(retry, capsys, damage):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    (retry.scope / "20261021_090000-other-zzzzzz.tag-attempts.json").write_text(
        damage, encoding="utf-8",
    )
    assert retry.fact() == REASON
    assert "[WARN] skipping" in capsys.readouterr().out


def test_an_unparsable_attempt_record_does_not_block_an_explicit_declaration(retry):
    (retry.scope / "20261021_090000-other-zzzzzz.tag-attempts.json").write_text(
        "{not json", encoding="utf-8",
    )
    assert retry.fact(gates_ran=True) is None


def test_a_record_that_names_this_tag_but_is_malformed_refuses(retry):
    (retry.scope / "20261021_090000-img-zzzzzz.tag-attempts.json").write_text(
        json.dumps({f"refs/tags/{TAG}": "not-an-oid"}), encoding="utf-8",
    )
    with pytest.raises(RefusedBeforeChange, match="malformed for"):
        retry.fact(gates_ran=True)


def test_a_record_for_another_tag_is_not_this_releases_business(retry):
    (retry.scope / "20261021_090000-img-zzzzzz.tag-attempts.json").write_text(
        json.dumps({"refs/tags/other-v1.0.0": "e" * 40}), encoding="utf-8",
    )
    with pytest.raises(RefusedBeforeChange, match="--gates-ran"):
        retry.fact()


def test_source_1_a_malformed_gates_record_refuses_instead_of_defaulting(retry):
    workspace = retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    (retry.scope / f"{transaction._release_token(workspace)}.gates.json").write_text(
        "{}", encoding="utf-8",
    )
    with pytest.raises(RefusedBeforeChange, match="malformed"):
        retry.fact()


def test_source_2_an_existing_body_that_states_the_skip_is_preserved(retry, capsys):
    _FakeReleases.body = f"img 1.2.3\n\n{LINE}\n\n## img-v1.2.3 - 2026-10-21\n- Fixed."
    assert retry.fact() == REASON
    assert "taken from the existing Release body" in capsys.readouterr().out


@pytest.mark.parametrize("body", [None, "img 1.2.3\n\n- Fixed.", "Released without gates (operator):  "])
def test_source_3_a_body_that_does_not_state_it_proves_nothing_and_refuses(retry, body):
    _FakeReleases.body = body
    with pytest.raises(RefusedBeforeChange, match="--gates-ran") as raised:
        retry.fact()
    assert "--gates-skipped REASON" in str(raised.value)
    assert RefusedBeforeChange.default_exit_code == exit_codes.REFUSED


def test_source_3_an_explicit_declaration_settles_an_undetermined_fact(retry):
    assert retry.fact(gates_ran=True) is None
    assert retry.fact(gates_skipped=REASON) == REASON


def test_an_explicit_declaration_cannot_contradict_a_derived_fact(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=REASON)
    with pytest.raises(RefusedBeforeChange, match="contradicts"):
        retry.fact(gates_ran=True)
    assert retry.fact(gates_skipped=REASON) == REASON
    _FakeReleases.body = None


def test_an_explicit_gated_declaration_cannot_contradict_a_recorded_gated_release(retry):
    retry.record("20261021_100000-img-aaaaaa", skip=None)
    with pytest.raises(RefusedBeforeChange, match="was gated"):
        retry.fact(gates_skipped=REASON)


@pytest.mark.parametrize("skipped", [REASON, None])
def test_the_body_retry_hands_the_fact_to_the_publisher_step(monkeypatch, tmp_path, skipped):
    seen = {}
    monkeypatch.setattr(
        cli, "_prepare_oci_release_body_retry",
        lambda *a, **k: ("retry-project", TAG_COMMIT, "ghcr.io/o/img", "1.2.3"),
    )
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *a: None)
    monkeypatch.setattr(cli, "_retry_gates_fact", lambda *a, **k: skipped)
    monkeypatch.setattr(
        cli, "run_project_step", lambda *a, protected_env: seen.update(protected_env),
    )
    project = cli.ProjectConfig("img", {}, {}, prefix="img-v")

    cli._run_oci_release_body_retry(
        tmp_path, project, TAG, git_auth=None,
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None),
    )

    assert seen.get(GATES_SKIPPED_ENV) == skipped
    assert seen["CMRU_RELEASE_TAG"] == TAG


def test_a_caller_supplied_protected_value_wins_inside_run_project_step(tmp_path, monkeypatch):
    import subprocess

    root = tmp_path / "demo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "cmru.toml").write_text(_STEP_PROJECT, encoding="utf-8")
    (_root, configs, *_rest) = cli.load_config(root / "cmru.toml")
    seen = []
    monkeypatch.setattr("cmru.cli.execute_step", lambda *a, **kw: seen.append(kw))
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda *_a, **_k: False)

    cli.run_project_step(
        configs["demo"], "build", root, root / "logs", protected_env={GATES_SKIPPED_ENV: REASON},
    )

    assert seen[0]["protected_env"][GATES_SKIPPED_ENV] == REASON


def test_a_missing_local_tag_refuses_the_fact_lookup(retry, tmp_path):
    _git(tmp_path / "repo", "tag", "-d", TAG)
    with pytest.raises(RefusedBeforeChange, match="is absent"):
        retry.fact(gates_ran=True)
