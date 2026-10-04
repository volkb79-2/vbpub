from contextlib import nullcontext
from pathlib import Path

import pytest

from cmru import cli, transaction


@pytest.fixture(autouse=True)
def fake_release_preflight(monkeypatch):
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli.transaction, "write_release_tag_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_require_local_tag_inspection_support", lambda _root: None)
    monkeypatch.setattr(
        cli, "_project_git_tag_policy_at_snapshot",
        lambda _root, _base, project, **_kwargs: getattr(project, "git_tag", True),
    )
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda _root, _base, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )
    monkeypatch.setattr(cli.transaction, "clear_plan_refused", lambda *_args: None)


def test_release_uses_fetched_origin_and_moved_config_path_when_local_main_is_behind(
    monkeypatch, tmp_path, capsys,
):
    project = cli.ProjectConfig(
        "demo", {}, {}, project_root=tmp_path / "old" / "demo",
        prefix="demo-v", github_token="token",
    )
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 1)
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda *_args: {"demo": Path("new/demo/cmru.toml")},
    )
    workspace_args = {}
    overlays = []
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace_args.update(kwargs) or workspace)
    monkeypatch.setattr(
        cli.transaction, "copy_secret_overlays",
        lambda *args, **kwargs: overlays.append(
            (args[-1], kwargs.get("candidate_config_paths")),
        ),
    )
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(True),
    )
    exc = cli.main([
            "release", "demo", "--config", str(tmp_path / "cmru.orchestration.toml"),
            "--discard-logs-on-release", "--discard-artifacts-on-release",
        ])
    assert exc == 0
    assert workspace_args == {"base": "b" * 40, "scope": "demo", "source_git_root": tmp_path}
    assert overlays == [(
        [tmp_path / "old" / "demo" / "cmru.toml"], [Path("new/demo/cmru.toml")],
    )]
    output = capsys.readouterr().out
    assert "1 commit(s) behind origin/main" in output
    assert "Release transaction complete" in output


def test_release_ref_flag_overrides_the_ahead_of_origin_comparison_ref(monkeypatch, tmp_path):
    # KI-20: --ref reaches assert_local_main_not_ahead instead of the default
    # local "main" -- proves the CLI flag actually threads through, not just
    # that the function itself accepts a ref kwarg (already unit-tested).
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "b" * 40)
    seen_refs = []
    monkeypatch.setattr(
        cli.transaction, "assert_local_main_not_ahead",
        lambda _root, **kwargs: seen_refs.append(kwargs.get("ref")) or 0,
    )
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(True),
    )
    exc = cli.main([
            "release", "demo", "--config", str(tmp_path / "cmru.toml"),
            "--discard-logs-on-release", "--discard-artifacts-on-release",
            "--ref", "origin/main",
        ])
    assert exc == 0
    assert seen_refs == ["origin/main"]


def test_release_ref_flag_defaults_to_main_when_omitted(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", prefix="demo-v", github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "b" * 40)
    seen_refs = []
    monkeypatch.setattr(
        cli.transaction, "assert_local_main_not_ahead",
        lambda _root, **kwargs: seen_refs.append(kwargs.get("ref")) or 0,
    )
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "remove_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.transaction, "forget_release_scope", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args, **kwargs: transaction._SyncLocalMainResult(True),
    )
    exc = cli.main([
            "release", "demo", "--config", str(tmp_path / "cmru.toml"),
            "--discard-logs-on-release", "--discard-artifacts-on-release",
        ])
    assert exc == 0
    assert seen_refs == ["main"]
