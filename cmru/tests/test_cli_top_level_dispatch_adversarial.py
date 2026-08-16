from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def _cleanup(*, packages=None):
    return cli.CleanupConfig(
        release_tag_prefixes=[], keep_release_tags=[], ghcr_packages=[],
        ghcr_delete_packages=packages or [],
    )


def _github(token="repo-token"):
    return cli.GitHubConfig("owner", "repo", token, "org")


def _env():
    return cli.ReleaseEnvConfig({}, None)


def test_run_cleanup_requires_project_credential_before_any_cleanup(monkeypatch, tmp_path):
    project = cli.ProjectConfig(name="demo", env={}, steps={}, prefix="demo-v", github_token="")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "github_for_project", lambda *_: cli.GitHubConfig("o", "r", "", "org"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    with pytest.raises(RuntimeError, match="requires GITHUB_PUSH_PAT"):
        cli.run_cleanup_verb(tmp_path, {"demo": project}, ["demo"], _cleanup(), _github(), _env(), None, False)


def test_run_cleanup_deletes_declared_ghcr_packages_and_dry_run_is_non_mutating(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig(name="noprefix", env={}, steps={}, prefix=None, github_token="repo-token")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "github_for_project", lambda github, project: github)
    monkeypatch.setattr(cli, "cleanup_project_releases_and_tags", lambda *args: [])
    monkeypatch.setattr(cli, "_latest_version_for_prefix", lambda *args: "")
    monkeypatch.setattr(cli, "cleanup_project_step", lambda *args: False)
    monkeypatch.setattr(cli, "delete_package", lambda *args, **kwargs: calls.append(args))
    calls = []
    cli.run_cleanup_verb(tmp_path, {"noprefix": project}, ["noprefix"], _cleanup(packages=["one", "two"]), _github(), _env(), None, False)
    assert [call[1] for call in calls] == ["one", "two"]

    calls.clear()
    cli.run_cleanup_verb(tmp_path, {"noprefix": project}, ["noprefix"], _cleanup(packages=["one"]), _github(), _env(), None, True)
    assert calls == []
    assert "Would delete GHCR packages: one" in capsys.readouterr().out


def test_orchestrate_step_order_selection_and_unknown_order_refusal(monkeypatch, tmp_path):
    project = cli.ProjectConfig(name="demo", env={}, steps={})
    config = (tmp_path, {"demo": project}, ["demo"], ["demo"], ["build"],
              "step-first", {"build": ["demo"]}, _cleanup(), _github(), _env())
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    ran = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, log_dir: ran.append((project.name, step)))
    monkeypatch.setattr(cli.sys, "argv", ["cmru", "--build"])
    cli._orchestrate()
    assert ran == [("demo", "build")]

    bad = (tmp_path, {"demo": project}, ["demo"], ["demo"], ["build"],
           "step-first", {"build": ["missing"]}, _cleanup(), _github(), _env())
    monkeypatch.setattr(cli, "load_config", lambda _: bad)
    with pytest.raises(ValueError, match="Unknown project in step_project_order"):
        cli._orchestrate()
