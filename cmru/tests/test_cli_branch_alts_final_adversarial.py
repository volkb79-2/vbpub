from types import SimpleNamespace

from cmru import cli


def test_successful_release_and_package_deletes_complete_without_error(monkeypatch):
    monkeypatch.setattr(cli, "http_request", lambda *args: (204, "", {}))
    cli.delete_release("o", "r", "t", 1, False)
    cli.delete_package_version("o", "p", "t", 2, "org", False)
    cli.delete_package("o", "p", "t", "org", False)


def test_cleanup_release_without_numeric_id_is_ignored_without_matching_remote_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "list_releases", lambda *args: [{"tag_name": "demo-v1"}])
    monkeypatch.setattr(cli, "list_remote_tags_matching", lambda *args: [])
    deleted = []
    monkeypatch.setattr(cli, "delete_git_tag_remote", lambda *args: deleted.append(("remote", args[1])))
    monkeypatch.setattr(cli, "delete_git_tag_local", lambda *args: deleted.append(("local", args[1])))
    assert cli.cleanup_project_releases_and_tags(tmp_path, "o", "r", "t", "demo", [], False) == []
    assert deleted == []


def test_isolated_build_without_prepare_starts_with_gate_then_artifact(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, build_step="wheel", runner_steps={})
    seen = []
    monkeypatch.setattr(cli, "_run_project_steps", lambda root, configs, names, steps: seen.extend(steps))
    cli._run_isolated_build_projects(tmp_path, {"demo": project}, ["demo"])
    assert seen == ["run-tests", "wheel"]


def test_prepare_release_unchanged_changelog_does_not_log_update(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, changelog="CHANGES.md")
    monkeypatch.setattr(cli, "generate_release_changelog", lambda *args, **kwargs: False, raising=False)
    monkeypatch.setattr(cli, "_commit_prepared_generated", lambda *args: False)
    # The helper imports this dependency locally, so patch its defining module.
    import cmru.changelog as changelog
    monkeypatch.setattr(changelog, "generate_release_changelog", lambda *args, **kwargs: False)
    cli._prepare_release_projects(tmp_path, {"demo": project}, ["demo"])
    output = capsys.readouterr().out
    assert "generating release history" in output
    assert "updated CHANGES.md" not in output
