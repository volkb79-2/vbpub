from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def _loaded(tmp_path):
    project = cli.ProjectConfig("alpha", {}, {}, project_root=tmp_path / "alpha", prefix="alpha-v", github_token="token")
    return (
        tmp_path, {"alpha": project}, ["alpha"], ["alpha"], ["alpha"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (transaction.RevertResult(ok=True, reverted=False), "Nothing to revert on origin/main"),
        (transaction.RevertResult(ok=False, reverted=False), "Automatic revert did not apply cleanly"),
    ],
)
def test_release_failure_reports_distinct_revert_outcome(monkeypatch, tmp_path, capsys, result, message):
    config = _loaded(tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "a" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli.transaction, "promotion_landed", lambda *args: True)
    monkeypatch.setattr(cli.transaction, "read_release_progress", lambda *args: None)
    monkeypatch.setattr(cli.transaction, "revert_promotion", lambda *args, **kwargs: result)
    monkeypatch.setattr(cli.transaction, "sync_local_main", lambda *args: True)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(AssertionError("failed releases retain worktree")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--project", "alpha", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert message in captured.out + captured.err
