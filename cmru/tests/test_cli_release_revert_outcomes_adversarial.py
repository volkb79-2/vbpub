from contextlib import nullcontext

import pytest

from cmru import cli, transaction


@pytest.fixture(autouse=True)
def fake_git_family(monkeypatch):
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )


def _loaded(tmp_path):
    project = cli.ProjectConfig("alpha", {}, {}, project_root=tmp_path / "alpha", prefix="alpha-v", github_token="token")
    return (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], ["alpha"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def test_release_failure_retains_candidate_without_automatic_revert(monkeypatch, tmp_path, capsys):
    config = _loaded(tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli.transaction, "plan_was_refused", lambda *args: False)
    calls = []
    monkeypatch.setattr(cli.transaction, "promotion_landed", lambda *args: calls.append("promotion-check") or True)
    monkeypatch.setattr(cli.transaction, "revert_promotion", lambda *args, **kwargs: calls.append("revert"))
    monkeypatch.setattr(
        cli.transaction, "_sync_local_main_result",
        lambda *args: transaction._SyncLocalMainResult(True),
    )
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(AssertionError("failed releases retain worktree")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "alpha", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Release candidate was not promoted" in output
    assert "candidate branch" in output
    assert calls == []
