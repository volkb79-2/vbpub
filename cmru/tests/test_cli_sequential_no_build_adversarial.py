from pathlib import Path

from cmru import cli, transaction, version


def _project(name, *, git_tag):
    return cli.ProjectConfig(name, {}, {}, prefix=f"{name}-v", git_tag=git_tag)


def test_sequential_untagged_no_build_skips_publish_and_records_progress(monkeypatch, tmp_path, capsys):
    project = _project("demo", git_tag=False)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: calls.append(("progress", args[-1])))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: calls.append("gate"))
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args: calls.append("promote"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: calls.append("backup"))
    monkeypatch.setattr(cli, "_run_untagged_project", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build")))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    released = cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    )
    assert released == []
    assert calls == [("progress", "a" * 40), "prepare", "gate", "promote", "backup", ("progress", "b" * 40)]
    assert "skipped build/push" in capsys.readouterr().out


def test_sequential_tagged_no_build_persists_tag_without_project_publish(monkeypatch, tmp_path, capsys):
    project = _project("demo", git_tag=True)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: calls.append(("progress", args[-1])))
    monkeypatch.setattr(transaction, "write_release_result", lambda *args: calls.append(("result", args[-1])))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args: calls.append("gate"))
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args: calls.append("promote"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args: calls.append("backup"))
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append("tag"))
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *args: calls.append("push-tag"))
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build/publish")))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    released = cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    )
    assert released == []
    assert calls == [
        ("progress", "a" * 40), "prepare", "gate", "promote", "backup", "tag",
        "promote", "push-tag", ("result", "demo-v1.2.3"), ("progress", "b" * 40),
    ]
    assert "tagged demo-v1.2.3, skipped build/publish" in capsys.readouterr().out
