from cmru import cli


def test_empty_release_and_package_pages_terminate_without_inventing_items(monkeypatch):
    urls = []
    monkeypatch.setattr(cli, "load_json", lambda url, token: urls.append(url) or ([], None))
    assert cli.list_releases("o", "r", "t") == []
    assert cli.list_package_versions("o", "p", "t", "org") == []
    assert cli.list_container_packages("o", "t", "user") == []
    assert len(urls) == 3
    assert all("page=1" in url for url in urls)


def test_run_cleanup_requires_repository_credential_for_explicit_ghcr_deletion(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix=None, github_token="project-token")
    github = cli.GitHubConfig("owner", "repo", "", "org")
    cleanup = cli.CleanupConfig([], [], [], ["private-package"])
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "apply_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "github_for_project", lambda *args: cli.GitHubConfig("owner", "repo", "project-token", "org"))
    with __import__("pytest").raises(RuntimeError, match="Repository-wide GHCR cleanup requires"):
        cli.run_cleanup_verb(tmp_path, {"demo": project}, ["demo"], cleanup, github, cli.ReleaseEnvConfig({}, None), None, False)


def test_run_no_longer_accepts_remote_cleanup_mode(capsys):
    assert cli.main(["run", "--remove-assets", "30d", "--dry-run"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err
