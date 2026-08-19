import pytest

from cmru import cli, transaction, version


def _config(tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


@pytest.mark.parametrize(
    ("no_build", "released", "message"),
    [
        (False, ["demo (demo-v1.2.3)"], "Released: demo (demo-v1.2.3)"),
        (True, [], "Tagged only (--no-build); nothing built or published."),
    ],
)
def test_child_release_persists_scope_pushes_backup_and_reports_completion(monkeypatch, tmp_path, capsys, no_build, released, message):
    config = _config(tmp_path)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *args, **kwargs: [("demo", "changed")])
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, "a" * 40)
    scope = []
    monkeypatch.setattr(transaction, "write_release_scope", lambda *args: scope.append(args[-1]))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: scope.append("backup"))
    monkeypatch.setattr(cli, "_release_projects_sequentially", lambda *args, **kwargs: released)
    cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")] + (["--no-build"] if no_build else []))
    assert scope == [["demo"], "backup"]
    assert message in capsys.readouterr().out
