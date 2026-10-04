from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


@pytest.fixture(autouse=True)
def fake_git_family(monkeypatch):
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(transaction, "clear_plan_refused", lambda *_args: None)
    monkeypatch.setattr(
        cli, "_project_git_tag_policy_at_snapshot",
        lambda _root, _base, project, **_kwargs: getattr(project, "git_tag", True),
    )
    monkeypatch.setattr(
        cli, "_project_config_paths_in_candidate",
        lambda _source, _candidate, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )


def test_release_resume_cleans_workspace_and_reports_sync_failure(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "retained", "cmru/release/resume", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(
        cli, "_project_release_policy_in_candidate",
        lambda _candidate, name, _config: (f"{name}-v", True),
    )
    monkeypatch.setattr(
        cli, "_assert_resume_candidate_is_safe_to_replay",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli.transaction, "project_git_family_groups", lambda root, projects: {root: list(projects)})
    monkeypatch.setattr(cli.transaction, "read_release_scope_for_path", lambda _path: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "resume_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "assert_resume_workspace_committed", lambda _path: None)
    calls = []
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args, **kwargs: calls.append("copy"))
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: calls.append(("child", args[1], kwargs)) or 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda w, **kwargs: calls.append("backup"))
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda w, **kwargs: calls.append("workspace"))
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args, **kwargs: calls.append("forget"))
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(
            False,
            "Could not sync local main automatically: caller checkout is dirty; local main was left untouched.",
        ),
    )
    exc = cli.main([
            "release", "--resume", str(workspace.path), "--config", str(tmp_path / "cmru.toml"),
            "--discard-logs-on-release", "--discard-artifacts-on-release",
        ])
    assert exc == 0
    assert calls[:2] == [
        "copy",
        ("child", ["demo", "--discard-logs-on-release", "--discard-artifacts-on-release", "--config", "cmru.toml"], {"project_names": ["demo"]}),
    ]
    assert calls[2:] == ["backup", "workspace", "forget"]
    output = capsys.readouterr().out
    assert "Could not sync local main automatically" in output
    assert "caller checkout is dirty" in output
    assert "rebase conflict" not in output
    assert "isolated worktree removed" in output


def test_release_resume_plan_refusal_keeps_existing_candidate(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig(
        "demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v",
        github_token="token",
    )
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("o", "r", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "retained", "cmru/release/resume", "a" * 40,
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(
        cli, "_project_release_policy_in_candidate",
        lambda _candidate, name, _config: (f"{name}-v", True),
    )
    monkeypatch.setattr(
        cli, "_assert_resume_candidate_is_safe_to_replay",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(transaction, "read_release_scope_for_path", lambda _path: ["demo"])
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(transaction, "resume_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(transaction, "assert_resume_workspace_committed", lambda _path: None)
    monkeypatch.setattr(transaction, "copy_secret_overlays", lambda *args, **kwargs: None)
    monkeypatch.setattr(transaction, "run_child", lambda *args, **kwargs: 2)
    monkeypatch.setattr(transaction, "plan_was_refused", lambda *_args: True)
    monkeypatch.setattr(
        transaction, "remove_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("candidate was removed")),
    )
    monkeypatch.setattr(
        transaction, "remove_backup_branch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("backup branch was removed")),
    )
    monkeypatch.setattr(
        transaction, "forget_release_scope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scope was forgotten")),
    )

    result = cli.main([
        "release", "--resume", str(workspace.path), "--config", str(tmp_path / "cmru.toml"),
    ])

    assert result == 2
    assert str(workspace.path) in capsys.readouterr().err


def test_resume_refuses_tag_pushed_for_an_incomplete_project(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-test", "a" * 40,
    )
    project = SimpleNamespace(prefix="demo-v", git_tag=True)
    baseline = {"refs/tags/demo-v1.0.0": "1" * 40}
    current = {**baseline, "refs/tags/demo-v1.1.0": "2" * 40}
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: {})
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_args: baseline)
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts",
        lambda *_args: {"refs/tags/demo-v1.1.0": "2" * 40},
    )
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: {},
    )
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: current)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: current)

    with pytest.raises(RuntimeError, match="origin release tags for demo changed"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_allows_a_tag_attempt_confirmed_absent_from_local_and_origin(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-test", "a" * 40,
    )
    project = SimpleNamespace(prefix="demo-v", git_tag=True)
    baseline = {"refs/tags/demo-v1.0.0": "1" * 40}
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: {})
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_args: baseline)
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts",
        lambda *_args: {"refs/tags/demo-v1.1.0": "2" * 40},
    )
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts",
        lambda *_args: {"refs/tags/demo-v1.1.0": "2" * 40},
    )
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: baseline)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: baseline)

    assert cli._assert_resume_candidate_is_safe_to_replay(
        tmp_path, workspace, ["demo"], {"demo": project},
        git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
    ) is None


def test_resume_refuses_absent_tag_attempt_without_cmrus_confirmation(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-test", "a" * 40,
    )
    project = SimpleNamespace(prefix="demo-v", git_tag=True)
    baseline = {"refs/tags/demo-v1.0.0": "1" * 40}
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: {})
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_args: baseline)
    monkeypatch.setattr(
        transaction, "read_release_tag_attempts",
        lambda *_args: {"refs/tags/demo-v1.1.0": "2" * 40},
    )
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: {},
    )
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: baseline)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: baseline)

    with pytest.raises(RuntimeError, match="no exact record proving origin confirmed"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_resume_uses_candidate_tag_prefix_when_checking_origin_tags(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-test", "a" * 40,
    )
    project = SimpleNamespace(prefix="caller-v", git_tag=True)
    baseline = {}
    current = {"refs/tags/snapshot-v1.0.0": "2" * 40}
    monkeypatch.setattr(transaction, "read_release_results", lambda *_args: {})
    monkeypatch.setattr(transaction, "read_release_tag_snapshot", lambda *_args: baseline)
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: {})
    monkeypatch.setattr(
        transaction, "read_confirmed_absent_release_tag_attempts", lambda *_args: {},
    )
    monkeypatch.setattr(transaction, "list_local_tag_refs", lambda *_args: baseline)
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: current)

    with pytest.raises(RuntimeError, match="origin release tags for demo changed"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
            release_policies={"demo": ("snapshot-v", True)},
        )


def test_resume_refuses_a_published_result_not_promoted_to_origin_main(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-test", "a" * 40,
    )
    project = SimpleNamespace(prefix="demo-v", git_tag=True)
    candidate = "b" * 40
    tag = "demo-v1.1.0"
    remote_tags = {f"refs/tags/{tag}": candidate}
    monkeypatch.setattr(
        transaction, "read_release_results", lambda *_args: {"demo": tag},
    )
    monkeypatch.setattr(transaction, "read_release_tag_attempts", lambda *_args: {})
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: remote_tags)
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda *_args, **_kwargs: "c" * 40)
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="not in origin/main"):
        cli._assert_resume_candidate_is_safe_to_replay(
            tmp_path, workspace, ["demo"], {"demo": project},
            git_auth=cli.GitHubGitAuth("owner", "repo", "token"),
        )


def test_project_release_policy_in_candidate_reads_snapshot_config(monkeypatch, tmp_path):
    candidate = tmp_path / "candidate"
    candidate_config = Path("demo/cmru.toml")
    content = """
[project]
id = "demo"
prefix = "snapshot-v"

[project.release]
git_tag = false
"""
    monkeypatch.setattr(
        cli, "run_local_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
    )
    monkeypatch.setattr(
        cli, "_read_git_path_at_commit",
        lambda root, revision, path, **_kwargs: content,
    )

    assert cli._project_release_policy_in_candidate(
        candidate, "demo", candidate_config,
    ) == ("snapshot-v", False)
