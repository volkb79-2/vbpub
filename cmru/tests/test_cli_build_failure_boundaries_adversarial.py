from contextlib import nullcontext
from pathlib import Path

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
    monkeypatch.setattr(
        cli.transaction,
        "project_git_family_groups",
        lambda root, projects: {root: list(projects)},
    )
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli.transaction, "release_lock", lambda _: nullcontext())
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {})
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_, **__: "b" * 40)
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda _root, _base, _config, _configs, names: {
            name: Path(name) / "cmru.toml" for name in names
        },
    )
    monkeypatch.setattr(cli.transaction, "assert_local_main_not_ahead", lambda *_, **__: 0)
    monkeypatch.setattr(cli.transaction, "create_workspace", lambda *args, **kwargs: workspace)
    return workspace


def test_build_with_uncommitted_project_paths_is_a_refusal_exit_4(monkeypatch, tmp_path, capsys):
    _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_uncommitted_release_paths", lambda *args: {"demo": ["demo/x.py"]})
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *a, **k: pytest.fail("a refused build must not create a worktree"),
    )
    assert cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")]) == 4
    err = capsys.readouterr().err
    assert "demo: uncommitted changes" in err
    assert "cmru build snapshots origin/main" in err


def test_build_while_the_release_lock_is_held_is_a_refusal_exit_4(monkeypatch, tmp_path, capsys):
    from contextlib import contextmanager

    _prepare_build(monkeypatch, tmp_path)

    @contextmanager
    def held(_root):
        raise cli.transaction.ReleaseLockHeld("Another cmru release transaction is already running.")
        yield

    monkeypatch.setattr(cli.transaction, "release_lock", held)
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *a, **k: pytest.fail("a refused build must not create a worktree"),
    )
    assert cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")]) == 4
    assert "already running" in capsys.readouterr().err


def test_build_failing_after_the_snapshot_started_stays_exit_1(monkeypatch, tmp_path, capsys):
    _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli.transaction, "fetch_origin_main",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("origin unreachable")),
    )
    assert cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")]) == 1
    assert "origin unreachable" in capsys.readouterr().err


def test_build_child_failure_retains_worktree_and_propagates_status(monkeypatch, tmp_path, capsys):
    workspace = _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 7)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: (_ for _ in ()).throw(AssertionError("retain")))
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(AssertionError("remove")))
    exc = cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 7
    assert "worktree retained for debugging" in capsys.readouterr().err
    assert workspace.path.name == "child"


def test_build_retention_failure_keeps_worktree_and_returns_generic_failure(monkeypatch, tmp_path, capsys):
    _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: (_ for _ in ()).throw(RuntimeError("missing logs")))
    removed = []
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda workspace: removed.append(workspace))
    exc = cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    assert removed == []
    assert "retention failed" in capsys.readouterr().err


def test_build_cleanup_failure_reports_retained_outputs_and_does_not_hide_error(monkeypatch, tmp_path, capsys):
    workspace = _prepare_build(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    retained = [tmp_path / "demo" / "artifacts" / "build-id"]
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: retained)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *args: (_ for _ in ()).throw(RuntimeError("busy worktree")))
    exc = cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert exc == 1
    output = capsys.readouterr().err
    assert "outputs were retained but worktree cleanup failed" in output
    assert "busy worktree" in output
    assert str(workspace.path) in output


def test_dirty_retained_build_does_not_suggest_publication(monkeypatch, tmp_path, capsys):
    _prepare_build(monkeypatch, tmp_path)
    retained = [
        tmp_path / "demo" / "logs" / "build-id",
        tmp_path / "demo" / "artifacts" / "build-id",
    ]
    monkeypatch.setattr(cli.transaction, "run_child", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli.transaction, "retain_successful_build_outputs", lambda *args: retained)
    monkeypatch.setattr(cli.transaction, "remove_workspace", lambda *_args: None)
    monkeypatch.setattr(
        cli.transaction,
        "validate_retained_build_output",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(
            "demo: retained build output has source tree changes; only a clean source tree can be published"
        )),
    )

    assert cli.main(["build", "demo", "--config", str(tmp_path / "cmru.toml")]) == 0
    output = capsys.readouterr()
    combined = output.out + output.err
    assert "not eligible for publication" in combined
    assert "source tree changes" in combined
    assert "publish these exact retained bytes" not in combined
