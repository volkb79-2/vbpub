"""Behavioural tests for residual CMRU CLI helper and dispatch branches."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli


def test_path_resolution_preserves_absolute_inputs_and_resolves_relative_inputs(tmp_path):
    config_path = tmp_path / "project" / "cmru.toml"
    assert cli.resolve_repo_root(config_path, "/srv/repo") == Path("/srv/repo")
    assert cli.resolve_repo_root(config_path, "..") == tmp_path.resolve()
    assert cli.resolve_cwd(tmp_path, "/srv/work") == Path("/srv/work")
    assert cli.resolve_cwd(tmp_path, "child") == (tmp_path / "child").resolve()


@pytest.mark.parametrize(
    "project, message",
    [
        ({"artifacts": ["wheel"], "release": "nope"}, "release must be a table"),
        ({"artifacts": ["wheel"], "release": {"git_tag": True, "commit_generated": "VERSION"}}, "commit_generated"),
        ({"artifacts": ["wheel"], "release": {"git_tag": "yes"}}, "git_tag"),
    ],
)
def test_release_policy_rejects_non_contract_shapes(project, message):
    with pytest.raises(ValueError, match=message):
        cli._parse_release_policy(project, "demo", None)


def test_release_policy_filters_empty_artifacts_and_supports_explicit_no_version_tag():
    spec = cli.VersionSpec("none", "patch", (), "1.0.0", "VERSION")
    result = cli._parse_release_policy(
        {"artifacts": ["wheel", "", "bundle"], "release": {"git_tag": False}},
        "demo", spec,
    )
    assert result == (("wheel", "bundle"), False, ())
    assert cli._bare_prefix("demo-v") == "demo"
    assert cli._bare_prefix("demo") == "demo"
    assert cli._bare_prefix(None) == ""


def test_apply_release_env_and_credentials_have_explicit_clear_and_refuse_paths(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "stale")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "stale")
    github = cli.GitHubConfig("owner", "repo", "", "org")
    cli.apply_release_env(github, cli.ReleaseEnvConfig({"MODE": "test", "EMPTY": ""}, None))
    assert os.environ["GITHUB_USERNAME"] == "owner"
    assert os.environ["GITHUB_REPO"] == "repo"
    assert "GITHUB_TOKEN" not in os.environ and "GITHUB_PUSH_PAT" not in os.environ
    assert os.environ["EMPTY"] == ""
    project = SimpleNamespace(github_token="", env={})
    with pytest.raises(RuntimeError, match="project"):
        cli.require_project_publish_credentials({"demo": project}, ["demo"])
    cli.require_project_publish_credentials({"demo": SimpleNamespace(github_token="tok")}, ["demo"])


def test_resolve_versions_from_git_exports_reproducible_metadata_and_exact_tag(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "README").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "tag", "demo-v1.2.3"], check=True)
    project = SimpleNamespace(prefix="demo-v", scm_dist="demo-dist")
    for key in ("SOURCE_DATE_EPOCH", "OCI_CREATED", "OCI_REVISION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_DIST"):
        monkeypatch.delenv(key, raising=False)
    cli.resolve_versions_from_git(tmp_path, {"demo": project})
    assert os.environ["SOURCE_DATE_EPOCH"].isdigit()
    assert os.environ["OCI_REVISION"]
    assert os.environ["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_DIST"] == "1.2.3"
    cli.resolve_versions_from_git(tmp_path, None)


def test_resolve_versions_ignores_projects_without_scm_facts(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "README").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    monkeypatch.delenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO", raising=False)
    cli.resolve_versions_from_git(tmp_path, {"demo": SimpleNamespace(prefix="", scm_dist="demo")})
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO" not in os.environ


def test_list_releases_paginates_and_stops_on_empty_page(monkeypatch):
    calls = []
    pages = [[{"id": n} for n in range(100)], [{"id": 101}], []]
    monkeypatch.setattr(cli, "load_json", lambda url, token: (calls.append(url) or pages.pop(0), {}))
    releases = cli.list_releases("o", "r", "t")
    assert len(releases) == 101
    assert "page=2" in calls[1]


def test_main_orchestration_builds_selected_steps_and_refuses_missing_credentials(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", github_token="", env={}, prefix="demo-v")
    loaded = (tmp_path, {"demo": project}, ["demo"], ["demo"], ["run-tests"], "project-first", {},
              cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "", "org"), cli.ReleaseEnvConfig({}, None))
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda *_: loaded)
    with pytest.raises(RuntimeError, match="Publishing"):
        cli.main(["run", "--push", "--config", "x"])


def test_version_helpers_only_accept_their_declared_git_describe_grammar(monkeypatch):
    assert cli._dev_version_from_describe("cmru-v1.2.3-4-gabcdef") == "1.2.4.dev4+gabcdef"
    assert cli._dev_version_from_describe("cmru-v1.2.3-0-gabcdef") == "1.2.3"
    assert cli._dev_version_from_describe("unrelated") is None
    monkeypatch.setattr(cli, "_source_tree_version", lambda: None)
    monkeypatch.setattr("importlib.metadata.version", lambda _: "9.9.9")
    assert cli._cmru_version() == "9.9.9"
