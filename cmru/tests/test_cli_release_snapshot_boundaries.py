"""Small behavioral boundaries for multi-family release handoff."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, config


def test_child_release_args_rebases_a_relative_config_from_the_git_root(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)

    args = cli._child_release_args(
        ["demo", "--resume", ".worktrees/candidate", "--config", "old.toml"],
        Path("cmru.orchestration.toml"), root,
        source_git_root=root, target_override="demo", original_target="demo",
    )

    assert args == ["demo", "--config", "cmru.orchestration.toml"]


def test_tag_subset_rejects_a_peeled_ref_without_its_direct_tag():
    with pytest.raises(RuntimeError, match="peeled release tag.*without its direct ref"):
        cli._tag_origin_ref_subset(
            {"refs/tags/demo-v1^{}": "a" * 40}, "demo-v1",
        )


def test_failed_tag_push_reports_when_guarded_cleanup_left_a_local_ref(
    monkeypatch, tmp_path,
):
    observed = []
    monkeypatch.setattr(cli, "local_git_tag_oid", lambda *_args, **_kwargs: "a" * 40)
    monkeypatch.setattr(cli.transaction, "write_release_tag_attempts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "run_remote_git",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )
    monkeypatch.setattr(cli, "_read_origin_tag_refs", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "delete_git_tag_local", lambda *_args, **_kwargs: None)

    def still_present(_root, _tag, *, action=None):
        observed.append(action)
        return "a" * 40

    monkeypatch.setattr(cli, "local_git_tag_oid", still_present)

    with pytest.raises(RuntimeError, match="could not remove every origin-absent local tag"):
        cli._push_tags(tmp_path, ["demo-v1"], workspace=SimpleNamespace())
    assert observed == [None, "recheck"]


def test_multi_family_dispatch_refuses_a_short_snapshot_handoff_write(monkeypatch, tmp_path):
    root_a = tmp_path / "family-a"
    root_b = tmp_path / "family-b"
    root_a.mkdir()
    root_b.mkdir()
    project_a = SimpleNamespace(name="alpha")
    project_b = SimpleNamespace(name="beta")
    groups = {root_a: [project_a], root_b: [project_b]}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: groups,
    )
    calls = []

    def short_write(fd, payload):
        calls.append(payload)
        return max(0, len(payload) - 1)

    monkeypatch.setattr(os, "write", short_write)

    with pytest.raises(RuntimeError, match="short write while handing off release snapshot"):
        cli._dispatch_independent_git_families(
            "release", [], tmp_path / "cmru.orchestration.toml", tmp_path,
            {"alpha": project_a, "beta": project_b}, ["alpha", "beta"],
            original_target=None,
            origin_main_snapshots={root_a: "a" * 40, root_b: "b" * 40},
        )
    assert calls == [f"{root_a.resolve()}:{'a' * 40}".encode("utf-8")]


def test_explicit_relative_project_config_is_resolved_from_current_directory(
    monkeypatch, tmp_path,
):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    selected = project_root / "cmru.toml"
    selected.write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config, "load_forge_config",
        lambda path, **_kwargs: SimpleNamespace(projects={"demo": object()}, orchestration=None),
    )
    monkeypatch.setattr(config, "_git_scope", lambda _path: {})

    context = config.resolve_invocation_context(Path("demo/cmru.toml"), cwd=tmp_path)

    assert context.config_path == selected
    assert context.config_reference_path == selected
    assert context.scope == "project"


def _release_config(tmp_path: Path, names: list[str]):
    projects = {
        name: cli.ProjectConfig(
            name, {}, {}, project_root=tmp_path / name, prefix=f"{name}-v",
            github_token="test-token",
        )
        for name in names
    }
    config_value = (
        tmp_path, projects, names, names, names, "project-first", {},
        cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "test-token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    return projects, config_value


def _stub_release_launcher(monkeypatch, tmp_path, projects, config_value, groups):
    monkeypatch.setattr(cli, "_read_release_snapshot_handoff_from_pipe", lambda: "private-handoff")
    monkeypatch.setattr(cli, "_resolve_config", lambda _path: tmp_path / "cmru.orchestration.toml")
    monkeypatch.setattr(cli, "load_config", lambda _path: config_value)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_configure_native_release_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_args: None)
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root: False)
    monkeypatch.setattr(cli.transaction, "project_git_family_groups", lambda *_args: groups)


def test_release_rejects_internal_handoff_on_dry_run(monkeypatch, tmp_path, capsys):
    projects, config_value = _release_config(tmp_path, ["demo"])
    _stub_release_launcher(
        monkeypatch, tmp_path, projects, config_value,
        {tmp_path: [projects["demo"]]},
    )

    assert cli.main(["release", "--dry-run", "--config", "cmru.orchestration.toml"]) == 1
    assert "valid only for a new family release launcher" in capsys.readouterr().err


def test_release_rejects_an_internal_snapshot_spanning_multiple_git_families(
    monkeypatch, tmp_path, capsys,
):
    projects, config_value = _release_config(tmp_path, ["alpha", "beta"])
    family_a = tmp_path / "family-a"
    family_b = tmp_path / "family-b"
    groups = {
        family_a: [projects["alpha"]],
        family_b: [projects["beta"]],
    }
    _stub_release_launcher(monkeypatch, tmp_path, projects, config_value, groups)

    assert cli.main(["release", "--config", "cmru.orchestration.toml"]) == 1
    assert "snapshot handoff cannot span Git families" in capsys.readouterr().err


def test_release_accepts_a_handoff_for_one_new_family_before_preflight(
    monkeypatch, tmp_path, capsys,
):
    projects, config_value = _release_config(tmp_path, ["demo"])
    _stub_release_launcher(
        monkeypatch, tmp_path, projects, config_value,
        {tmp_path: [projects["demo"]]},
    )
    calls = []

    def stop_after_handoff(*_args, **_kwargs):
        calls.append("preflight")
        raise RuntimeError("controlled preflight stop")

    monkeypatch.setattr(cli, "_preflight_multi_family_release_tag_support", stop_after_handoff)

    assert cli.main(["release", "--config", "cmru.orchestration.toml"]) == 1
    error = capsys.readouterr().err
    assert calls == ["preflight"]
    assert "controlled preflight stop" in error
    assert "valid only for a new family release launcher" not in error
