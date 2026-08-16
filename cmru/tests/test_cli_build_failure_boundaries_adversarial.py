from contextlib import nullcontext

import pytest

from cmru import cli, transaction


def _config(tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, project_root=tmp_path / "demo", build_step="build")
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


def _prepare_build(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/build/fail", "a" * 40)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_: "b" * 40)
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(cli.transaction, "copy_secret_overlays", lambda *args: None)
    return workspace


def test_build_child_failure_retains_worktree_and_propagates_status(monkeypatch, tmp_path, capsys):
    workspace = _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 7)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: (_ for _ in ()).throw(AssertionError("retain")))
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(AssertionError("remove")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 7
    assert "worktree retained for debugging" in capsys.readouterr().err
    assert workspace.path.name == "child"


def test_build_retention_failure_keeps_worktree_and_returns_generic_failure(monkeypatch, tmp_path, capsys):
    _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: (_ for _ in ()).throw(RuntimeError("missing logs")))
    removed = []
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda workspace: removed.append(workspace))
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 1
    assert removed == []
    assert "retention failed" in capsys.readouterr().err


def test_build_cleanup_failure_reports_retained_outputs_and_does_not_hide_error(monkeypatch, tmp_path, capsys):
    workspace = _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    retained = [tmp_path / "demo" / "artifacts" / "build-id"]
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: retained)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(RuntimeError("busy worktree")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["build", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc.value.code == 1
    output = capsys.readouterr().err
    assert "outputs were retained but worktree cleanup failed" in output
    assert "busy worktree" in output
    assert str(workspace.path) in output
