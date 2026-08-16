from cmru import cli


def test_container_package_pagination_advances_after_full_page(monkeypatch):
    urls = []

    def load(url, token):
        urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        if page == 1:
            return ([{"name": "pkg-%d" % index} for index in range(100)], None)
        return ([], None)

    monkeypatch.setattr(cli, "load_json", load)
    packages = cli.list_container_packages("owner", "token", "org")
    assert len(packages) == 100
    assert urls[-1].endswith("page=2")


def test_publish_dispatch_runs_only_declared_push_step(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, runner_steps={"push": []}, github_token="token")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("owner", "repo", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(cli, "resolve_versions_from_git", lambda *_: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_: None)
    ran = []
    monkeypatch.setattr(cli, "run_project_step", lambda project, step, root, logs: ran.append((project.name, step)))
    cli.main(["publish", "--project", "demo", "--config", str(tmp_path / "cmru.toml")])
    assert ran == [("demo", "push")]
