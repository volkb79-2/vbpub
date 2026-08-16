from types import SimpleNamespace

import pytest

from cmru import changelog, cli


def test_release_env_handles_missing_owner_or_repo_without_inventing_values(monkeypatch):
    monkeypatch.delenv("GITHUB_USERNAME", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    cli.apply_release_env(cli.GitHubConfig("", "repo", "", "user"), cli.ReleaseEnvConfig({}, None))
    assert "GITHUB_USERNAME" not in __import__("os").environ
    assert __import__("os").environ["GITHUB_REPO"] == "repo"
    cli.apply_release_env(cli.GitHubConfig("owner", "", "", "user"), cli.ReleaseEnvConfig({}, None))
    assert __import__("os").environ["GITHUB_USERNAME"] == "owner"


def test_delete_release_http_error_and_remote_tag_malformed_ref_fail_or_skip(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "http_request", lambda *args: (500, "bad", {}))
    with pytest.raises(RuntimeError, match="Failed to delete release 4"):
        cli.delete_release("o", "r", "t", 4, False)
    class Result:
        returncode = 0
        stdout = "abc\trefs/not-tags/v1\n"
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Result())
    assert cli.list_remote_tags_matching(tmp_path, "v*") == []


def test_isolated_build_includes_prepare_phase_when_declared(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, build_step="wheel", runner_steps={"prepare": []})
    phases = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda root, configs, names, steps: phases.extend(steps))
    cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    assert phases == ["prepare", "run-tests", "wheel"]


def test_release_prepare_changelog_changed_path_logs_update(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {"prepare": []}, changelog="CHANGES.md")
    monkeypatch.setattr(cli, "run_project_step", lambda *args: None)
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *args: True)
    cli._prepare_release_projects(tmp_path, {"demo": project}, ["demo"])
    assert "updated CHANGES.md" in capsys.readouterr().out
