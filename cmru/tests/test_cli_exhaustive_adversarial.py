"""High-value main-dispatch tests for CMRU CLI transaction and cleanup modes."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction


def _project(name="demo", token="tok"):
    return SimpleNamespace(name=name, cwd=name, project_root=Path(name), paths=[name], prefix=f"{name}-v",
                           github_token=token, env={}, steps={}, runner_steps={}, changelog="CHANGES.md")


def _loaded(tmp_path, project=None):
    project = project or _project()
    return (tmp_path, {project.name: project}, [project.name], [project.name], [], "project-first", {},
            SimpleNamespace(), cli.GitHubConfig("o", "r", "tok", "user"), cli.ReleaseEnvConfig({}, None))


def test_main_cleanup_rejects_multiple_destructive_modes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--remove-assets", "1d", "--delete-build-output", "id", "--config", "x"])
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_main_cleanup_unmanaged_release_namespace_and_confirmation_guards(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--delete-unmanaged-release-tag", "demo-v1", "--config", "x"])
    assert exc.value.code == 2 and "requires --project" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--project", "demo", "--delete-unmanaged-release-tag", "demo-v1", "--config", "x"])
    assert exc.value.code == 2 and "requires --yes" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--project", "demo", "--yes", "--delete-unmanaged-release-tag", "other-v1", "--config", "x"])
    assert exc.value.code == 2 and "outside project" in capsys.readouterr().err


def test_main_cleanup_unmanaged_managed_and_dry_run_routes(monkeypatch, tmp_path, capsys):
    project = _project()
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    calls = []
    monkeypatch.setattr(cli, "delete_unmanaged_release_tag", lambda *args, **kwargs: calls.append((args, kwargs)))
    cli.main(["cleanup", "--project", "demo", "--dry-run", "--delete-unmanaged-release-tag", "demo-old", "--config", "x"])
    assert calls[0][0][2] == "tok" and calls[0][0][3] == "demo-old" and calls[0][1]["dry_run"] is True
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--project", "demo", "--yes", "--delete-unmanaged-release-tag", "demo-v1", "--config", "x"])
    assert exc.value.code == 2 and "CMRU-managed" in capsys.readouterr().err


def test_main_cleanup_build_output_and_discard_worktree_route_exact_targets(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path))
    deleted = []
    monkeypatch.setattr(transaction, "delete_retained_build_output", lambda *args, **kwargs: deleted.append((args, kwargs)) or [tmp_path / "logs/id"])
    cli.main(["cleanup", "--project", "demo", "--dry-run", "--delete-build-output", "20240101T000000Z_" + "a" * 40, "--config", "x"])
    assert deleted and deleted[0][1]["dry_run"] is True
    with pytest.raises(SystemExit) as exc:
        cli.main(["cleanup", "--project", "demo", "--discard-build-worktree", "/tmp/w", "--dry-run", "--config", "x"])
    assert exc.value.code == 2 and "do not pass --project" in capsys.readouterr().err
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "w", "cmru/build/x", "a" * 40)
    monkeypatch.setattr(transaction, "discard_build_workspace", lambda *args, **kwargs: workspace)
    cli.main(["cleanup", "--discard-build-worktree", "/tmp/w", "--dry-run", "--config", "x"])
    assert "Would discard" in capsys.readouterr().out


def test_main_release_child_dry_run_has_no_promotion_or_workspace_mutation(monkeypatch, tmp_path, capsys):
    project = _project()
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path, project))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr("cmru.version.detect_changed_projects", lambda *_, **__: [("demo", "patch")])
    calls = []
    monkeypatch.setattr("cmru.version.release_cmd", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(cli, "_transaction_workspace_from_env", lambda _root: transaction.ReleaseWorkspace(tmp_path, tmp_path, "cmru/release/x", "a" * 40))
    monkeypatch.setattr(transaction, "write_release_scope", lambda *args: calls.append("scope"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: calls.append("backup"))
    cli.main(["release", "--_transaction-child", "--dry-run", "--config", "x"])
    assert calls[0][1]["dry_run"] is True
    assert "DRY RUN" in capsys.readouterr().out


def test_main_status_rejects_non_orchestrated_project_before_status_cmd(monkeypatch, tmp_path, capsys):
    project = _project()
    loaded = _loaded(tmp_path, project)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: loaded)
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--project", "other", "--config", "x"])
    assert exc.value.code == 2 and "Unknown or non-orchestrated" in capsys.readouterr().err


def test_main_default_cleanup_routes_to_cleanup_verb_with_project_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _loaded(tmp_path))
    calls = []
    monkeypatch.setattr(cli, "run_cleanup_verb", lambda *args, **kwargs: calls.append((args, kwargs)))
    cli.main(["cleanup", "--project", "demo", "--dry-run", "--config", "x"])
    assert calls[0][1]["project_filter"] == "demo" and calls[0][1]["dry_run"] is True
