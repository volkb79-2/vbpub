"""W0-REL: in-process branch tests for the REL-05/REL-08/REL-12 code.

The end-to-end suite (``test_release_end_to_end_real_git.py``) runs the real CLI in
subprocesses, which the coverage lane cannot see, so every failure branch of the
new functions is pinned here in-process as well.
"""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, cli, transaction

_TAG = "demo-v1.0.0"
_REF = f"refs/tags/{_TAG}"
_OID = "a" * 40


def _workspace(tmp_path: Path) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(tmp_path, tmp_path / "w", "cmru-release-x", "b" * 40)


# --- _release_tag_recovery_commands / _report_publication_started -------------------

def test_recovery_commands_pin_the_tag_object_when_known_and_omit_it_otherwise():
    pinned = cli._release_tag_recovery_commands(_TAG, _OID, "branch-x")
    assert f"--force-with-lease={_REF}:{_OID} origin :{_REF}" in pinned
    assert "cmru abandon branch-x --yes" in pinned
    unpinned = cli._release_tag_recovery_commands(_TAG, None, "branch-x")
    assert f"--force-with-lease={_REF} origin" in unpinned
    assert f"{_REF}:" not in unpinned.split("origin")[0]


@pytest.mark.parametrize("attempts, expected", [
    ({_REF: _OID}, f"object id: {_OID}"),
    ({}, "object id: unknown"),
])
def test_report_publication_started_names_the_tag_and_its_object(
    monkeypatch, tmp_path, capsys, attempts, expected,
):
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_a: attempts)

    cli._report_publication_started(tmp_path, _workspace(tmp_path), _TAG)

    output = capsys.readouterr()
    text = output.out + output.err
    assert f"Publishing of {_TAG} had started" in text
    assert expected in text
    assert "--resume` refuses this candidate" in text


# --- _rollback_unpublished_release_tag ------------------------------------------------

class _Tags:
    """Scripted stand-ins for the guarded tag functions used by the rollback."""

    def __init__(self, monkeypatch, *, remote_after=None, local_after=None, attempts=None):
        self.calls: list[tuple] = []
        monkeypatch.setattr(
            transaction, "read_release_tag_attempts",
            lambda *_a: {_REF: _OID} if attempts is None else attempts,
        )
        monkeypatch.setattr(
            cli, "delete_git_tag_remote",
            lambda *a, **k: self.calls.append(("remote", k.get("expected_oid"))),
        )
        monkeypatch.setattr(
            cli, "list_remote_tag_refs_matching",
            lambda *a, **k: {} if remote_after is None else {_TAG: remote_after},
        )
        monkeypatch.setattr(
            cli, "delete_git_tag_local",
            lambda *a, **k: self.calls.append(("local", k.get("expected_oid"))),
        )
        monkeypatch.setattr(cli, "local_git_tag_oid", lambda *a, **k: local_after)
        monkeypatch.setattr(
            transaction, "write_confirmed_absent_release_tag_attempts",
            lambda _root, _ws, refs: self.calls.append(("absent", dict(refs))),
        )


def test_rollback_deletes_remote_then_local_pinned_to_the_pushed_object(
    monkeypatch, tmp_path, capsys,
):
    tags = _Tags(monkeypatch)
    workspace = _workspace(tmp_path)

    assert cli._rollback_unpublished_release_tag(
        tmp_path, workspace, _TAG, git_auth=None, cause=RuntimeError("build broke"),
    ) is True

    assert tags.calls == [("remote", _OID), ("local", _OID), ("absent", {_REF: _OID})]
    text = "".join(capsys.readouterr())
    assert "build broke" in text
    assert f"cmru release --resume {workspace.path}" in text


@pytest.mark.parametrize("kwargs, reason, recorded", [
    ({"attempts": {}}, "no recorded push attempt", False),
    ({"remote_after": _OID}, f"origin still has {_TAG} at {_OID}", True),
    ({"local_after": _OID}, f"the local tag remains at {_OID}", True),
])
def test_rollback_that_cannot_prove_absence_keeps_the_tag_and_prints_recovery(
    monkeypatch, tmp_path, capsys, kwargs, reason, recorded,
):
    tags = _Tags(monkeypatch, **kwargs)

    assert cli._rollback_unpublished_release_tag(
        tmp_path, _workspace(tmp_path), _TAG, git_auth=None, cause=RuntimeError("x"),
    ) is False

    assert not any(call[0] == "absent" for call in tags.calls)  # no false absence proof
    text = "".join(capsys.readouterr())
    assert reason in text
    assert "The tag was kept; nothing was published" in text
    assert "cmru abandon cmru-release-x --yes" in text
    assert (f":{_OID} origin" in text) is recorded


# --- _run_tagged_build_and_publish ---------------------------------------------------

class _Steps:
    def __init__(self, monkeypatch, *, fail_on=None, unchanged_fails=False):
        self.steps: list[list[str]] = []
        self.rolled_back: list[str] = []
        self.reported: list[str] = []

        def run(_root, _configs, _names, steps, **_kwargs):
            self.steps.append(list(steps))
            if fail_on in steps:
                raise RuntimeError(f"{fail_on} failed")

        def unchanged(*_args):
            if unchanged_fails:
                raise RuntimeError("candidate moved")

        monkeypatch.setattr(cli, "_run_project_steps", run)
        monkeypatch.setattr(cli, "_assert_release_candidate_unchanged", unchanged)
        monkeypatch.setattr(
            cli, "_rollback_unpublished_release_tag",
            lambda *_a, **k: self.rolled_back.append(str(k["cause"])) or True,
        )
        monkeypatch.setattr(
            cli, "_report_publication_started",
            lambda _root, _ws, tag: self.reported.append(tag),
        )

    def run(self, tmp_path, phases):
        cli._run_tagged_build_and_publish(
            tmp_path, {}, "demo", _workspace(tmp_path), _TAG, phases,
            candidate_sha=_OID, git_auth=None, github_config=None, env_config=None,
        )


def test_build_then_publish_run_as_separate_steps_without_any_recovery(monkeypatch, tmp_path):
    steps = _Steps(monkeypatch)
    steps.run(tmp_path, ["build"])
    assert steps.steps == [["build"], ["push"]]
    assert steps.rolled_back == [] and steps.reported == []


def test_a_prepare_only_build_step_skips_straight_to_publish(monkeypatch, tmp_path):
    steps = _Steps(monkeypatch)
    steps.run(tmp_path, [])
    assert steps.steps == [["push"]]


def test_a_failed_build_rolls_the_tag_back_and_reraises(monkeypatch, tmp_path):
    steps = _Steps(monkeypatch, fail_on="build")
    with pytest.raises(RuntimeError, match="build failed"):
        steps.run(tmp_path, ["build"])
    assert steps.steps == [["build"]]  # publishing never started
    assert steps.rolled_back == ["build failed"]
    assert steps.reported == []


@pytest.mark.parametrize("kwargs", [{"fail_on": "push"}, {"unchanged_fails": True}])
def test_a_failure_once_publishing_started_keeps_the_tag(monkeypatch, tmp_path, kwargs):
    steps = _Steps(monkeypatch, **kwargs)
    with pytest.raises(RuntimeError):
        steps.run(tmp_path, ["build"])
    assert steps.rolled_back == []  # public artifacts may exist: never roll back
    assert steps.reported == [_TAG]


# --- REL-08: the trailer label falls back to the project name ------------------------

def _prepare(monkeypatch, tmp_path, planner):
    labels: list[str | None] = []
    project = SimpleNamespace(
        name="demo", steps={}, changelog="CHANGES.md", git_tag=True,
    )
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *a, **k: False)
    monkeypatch.setattr(changelog, "pending_release_tag", planner)
    monkeypatch.setattr(
        cli, "_commit_prepared_generated",
        lambda _root, _project, label=None: labels.append(label),
    )
    cli._prepare_release_projects(tmp_path, {"demo": project}, ["demo"])
    return labels


def test_the_release_candidate_label_is_the_pending_tag(monkeypatch, tmp_path):
    assert _prepare(monkeypatch, tmp_path, lambda *a, **k: _TAG) == [_TAG]


@pytest.mark.parametrize("planner", [
    lambda *a, **k: None,  # a no-tag project has no tag
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no plan")),
])
def test_the_release_candidate_label_falls_back_to_the_project_name(
    monkeypatch, tmp_path, planner,
):
    assert _prepare(monkeypatch, tmp_path, planner) == ["demo"]


def test_pending_release_tag_names_the_tag_or_none(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", prefix="demo-v")
    monkeypatch.setattr(changelog, "_project_release_plan", lambda *a, **k: ("1.2.3", None))
    assert changelog.pending_release_tag(tmp_path, project) == "demo-v1.2.3"
    monkeypatch.setattr(changelog, "_project_release_plan", lambda *a, **k: (None, None))
    assert changelog.pending_release_tag(tmp_path, project) is None


# --- REL-02 helper branches ----------------------------------------------------------

def test_a_generated_section_without_a_cursor_is_treated_as_current(tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], commit_generated=())
    section = "## [1.0.0] - 2026-01-01\n<!-- cmru: generated -->\n"
    assert changelog._project_commits_after_cursor(
        tmp_path, project, tmp_path / "demo" / "CHANGES.md", section,
    ) is False


def test_an_unreadable_cursor_is_treated_as_current(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], commit_generated=())
    section = f"<!-- cmru: source-end={_OID} -->\n"

    def unknown_commit(*_args, **_kwargs):
        raise RuntimeError("bad object")

    monkeypatch.setattr(changelog, "_subject_groups", unknown_commit)
    assert changelog._project_commits_after_cursor(
        tmp_path, project, tmp_path / "demo" / "CHANGES.md", section,
    ) is False


# --- REL-13 helper -------------------------------------------------------------------

def test_an_uninspectable_ignored_collision_set_is_reported_as_unknown(monkeypatch, tmp_path):
    def broken(*_args, **_kwargs):
        raise RuntimeError("git failed")

    monkeypatch.setattr(transaction, "_git", broken)
    assert transaction._ignored_paths_origin_would_overwrite(tmp_path) is None


# --- pre-existing branch gaps on the integration branch (python 3.14 coverage) --------

def test_rotating_two_attempted_tags_reads_the_absence_proofs_once(tmp_path):
    from tests.test_release_tag_absence_proofs import _workspace as proof_workspace

    root, workspace = proof_workspace(tmp_path)
    first = {"refs/tags/demo-v1": "a" * 40, "refs/tags/demo-v2": "b" * 40}
    rotated = {"refs/tags/demo-v1": "c" * 40, "refs/tags/demo-v2": "d" * 40}
    transaction.write_release_tag_attempts(root, workspace, first)
    transaction.write_confirmed_absent_release_tag_attempts(root, workspace, first)

    transaction.write_release_tag_attempts(root, workspace, rotated)

    assert transaction.read_release_tag_attempts(root, workspace) == rotated


def test_symlink_resolution_never_loops_on_an_empty_pending_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "PurePosixPath", lambda *_a: SimpleNamespace(parts=()))
    with pytest.raises(RuntimeError, match="Symlink resolution exceeded its limit"):
        cli._resolve_git_file_at_commit(
            tmp_path, "HEAD", Path("a/b"), source_label="origin/main",
        )


# --- REL-12: the launcher's dry-run and untagged arcs --------------------------------

@pytest.fixture
def launcher_env(monkeypatch):
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_a, **_k: {})
    monkeypatch.setattr(cli.transaction, "write_release_tag_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(cli.transaction, "clear_plan_refused", lambda *_a: None)
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda _root, _base, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )
    monkeypatch.setattr(
        cli, "_project_config_paths_in_candidate",
        lambda _source, _candidate, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )
    monkeypatch.setattr(cli, "_assert_resume_candidate_is_safe_to_replay", lambda *a, **k: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "read_release_scope_for_path", lambda _p: ["demo"])
    monkeypatch.setattr(cli.transaction, "assert_resume_workspace_committed", lambda _p: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *a, **k: 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *a, **k: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *a, **k: None)
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *a, **k: transaction._SyncLocalMainResult(True),
    )


def _launch(monkeypatch, tmp_path, argv, *, tagged_policy: bool):
    project = cli.ProjectConfig(
        "demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token",
    )
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "retained", "cmru/release/x", "a" * 40,
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(
        cli, "_project_release_policy_in_candidate",
        lambda _candidate, name, _config: (f"{name}-v", tagged_policy),
    )
    monkeypatch.setattr(
        cli, "_project_git_tag_policy_at_snapshot",
        lambda _root, _base, _project, **_kwargs: tagged_policy,
    )
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *a, **k: workspace)
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_a, **_k: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_a, **_k: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *a, **k: workspace)
    inspected: list[Path] = []
    monkeypatch.setattr(
        cli, "_require_local_tag_inspection_support", lambda root: inspected.append(root),
    )
    code = cli.main([
        "release", *argv, "--config", str(tmp_path / "cmru.toml"),
        "--discard", "logs", "--discard", "artifacts",
    ])
    return code, inspected


def test_resume_dry_run_does_not_require_local_tag_inspection(
    launcher_env, monkeypatch, tmp_path,
):
    code, inspected = _launch(
        monkeypatch, tmp_path, ["--resume", str(tmp_path / "retained"), "--dry-run"],
        tagged_policy=True,
    )
    assert code == 0 and inspected == []


def test_resume_of_untagged_projects_does_not_require_local_tag_inspection(
    launcher_env, monkeypatch, tmp_path,
):
    code, inspected = _launch(
        monkeypatch, tmp_path, ["--resume", str(tmp_path / "retained")], tagged_policy=False,
    )
    assert code == 0 and inspected == []


def test_resume_of_tagged_projects_requires_local_tag_inspection(
    launcher_env, monkeypatch, tmp_path,
):
    code, inspected = _launch(
        monkeypatch, tmp_path, ["--resume", str(tmp_path / "retained")], tagged_policy=True,
    )
    assert code == 0 and inspected == [tmp_path]


def test_fresh_release_of_untagged_projects_does_not_require_local_tag_inspection(
    launcher_env, monkeypatch, tmp_path,
):
    code, inspected = _launch(monkeypatch, tmp_path, ["demo"], tagged_policy=False)
    assert code == 0 and inspected == []
