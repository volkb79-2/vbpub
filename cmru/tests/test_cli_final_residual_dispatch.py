import pytest

from cmru import cli, transaction, version


def _config(tmp_path, project):
    return (
        tmp_path, {project.name: project}, [project.name], [project.name], [project.name],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def test_cleanup_build_output_unknown_project_refuses_before_deletion(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(transaction, "delete_retained_build_output", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("delete")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--delete-build-output", "id", "--project", "missing", "--dry-run"])
    assert exc.value.code == 2
    assert "unknown project: missing" in capsys.readouterr().err


def test_release_child_dry_run_with_no_changes_is_a_noop(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *_, **__: [])
    release_calls = []
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: release_calls.append(kwargs))
    cli.main(["release", "--dry-run", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    assert release_calls == [{
        "project_filter": None, "minor": False, "major": False,
        "set_version": None, "dry_run": True,
    }]
    output = capsys.readouterr().out
    assert "Release plan: no changed projects detected" in output
    assert "No tags pushed" in output
