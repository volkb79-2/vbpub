from pathlib import Path

from cmru import cli


def test_tag_on_head_ignores_latest_pointer_when_no_version_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_git", lambda *args: "demo-latest\n")
    assert cli._tag_on_head(tmp_path, "demo-") is None


def test_cleanup_project_step_without_declared_step_is_a_noop(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {})
    monkeypatch.setattr(cli, "_build_step_config", lambda *args: (_ for _ in ()).throw(AssertionError("build")))
    assert cli.cleanup_project_step(tmp_path, project, "1.2.3", False) is False


def test_uncommitted_release_paths_skips_unknown_project_without_git_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("probe")))
    assert cli._uncommitted_release_paths(tmp_path, {}, ["missing"]) == {}


def test_commit_prepared_generated_returns_false_for_clean_worktree(monkeypatch, tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, cwd="demo", commit_generated=("generated.txt",))
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda *args, **kwargs: [])
    assert cli._commit_prepared_generated(tmp_path, project) is False
