import os
from types import SimpleNamespace

from cmru import cli


def test_release_environment_skips_none_but_exports_explicit_values(monkeypatch):
    monkeypatch.setenv("INHERITED", "ambient")
    github = cli.GitHubConfig("owner", "repo", "token", "org")
    env = cli.ReleaseEnvConfig({"INHERITED": None, "EXPLICIT": ""}, "registry.example")
    cli.apply_release_env(github, env)
    assert os.environ["INHERITED"] == "ambient"
    assert os.environ["EXPLICIT"] == ""
    assert os.environ["GITHUB_PUSH_PAT"] == "token"
    assert os.environ["REGISTRY"] == "registry.example"


def test_resolve_versions_does_not_export_empty_exact_tag(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", scm_dist="demo-dist")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="\n"))
    monkeypatch.delenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_DIST", raising=False)
    cli.resolve_versions_from_git(tmp_path, {"demo": project})
    assert "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO_DIST" not in os.environ


def test_package_delete_unexpected_http_failure_is_not_silently_ignored(monkeypatch):
    monkeypatch.setattr(cli, "http_request", lambda *args: (500, "server exploded", {}))
    try:
        cli.delete_package("owner", "pkg", "token", "org", False)
    except RuntimeError as exc:
        assert "Failed to delete pkg package" in str(exc)
        assert "server exploded" in str(exc)
    else:
        raise AssertionError("unexpected package deletion failure must be raised")


def test_tag_deletion_success_reports_real_mutation(monkeypatch, tmp_path, capsys):
    seen = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: seen.append(argv) or SimpleNamespace(returncode=0))
    cli.delete_git_tag_remote(tmp_path, "demo-v1", False)
    cli.delete_git_tag_local(tmp_path, "demo-v1", False)
    assert seen == [
        ["git", "-C", str(tmp_path), "push", "origin", ":refs/tags/demo-v1"],
        ["git", "-C", str(tmp_path), "tag", "-d", "demo-v1"],
    ]
    output = capsys.readouterr().out
    assert "Deleted remote tag demo-v1" in output
    assert "Deleted local tag demo-v1" in output


def test_orchestrate_all_selection_runs_requested_validate_step(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, runner_steps={"validate": []})
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    seen = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: seen.append((project.name, step)))
    monkeypatch.setattr(cli.sys, "argv", ["cmru", "--project", "all", "--validate"])
    cli._orchestrate()
    assert seen == [("demo", "validate")]


def test_source_tree_version_returns_none_when_git_describe_has_no_tag(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    assert cli._source_tree_version() is None
