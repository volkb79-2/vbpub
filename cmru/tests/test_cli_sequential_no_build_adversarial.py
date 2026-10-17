from pathlib import Path

import pytest

from cmru import cli, transaction, version


def _project(name, *, git_tag):
    return cli.ProjectConfig(name, {}, {}, prefix=f"{name}-v", git_tag=git_tag)


def test_sequential_untagged_no_build_skips_publish_and_records_progress(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    project = _project("demo", git_tag=False)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: calls.append(("progress", args[-1])))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args, **_kw: calls.append("gate"))
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: calls.append("promote"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: calls.append("backup"))
    monkeypatch.setattr(cli, "_run_untagged_project", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build")))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)
    released = cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    )
    assert released == []
    assert calls == [("progress", "a" * 40), "prepare", "backup", "gate", "promote", ("progress", "b" * 40)]
    assert "skipped build/push" in capsys.readouterr().out


def test_sequential_tagged_no_build_persists_tag_without_project_publish(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    project = _project("demo", git_tag=True)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: calls.append(("progress", args[-1])))
    monkeypatch.setattr(transaction, "write_release_result", lambda *args: calls.append(("result", args[-1])))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args, **_kw: calls.append("gate"))
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: calls.append("promote"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: calls.append("backup"))
    monkeypatch.setattr(
        cli, "_assert_release_candidate_unchanged",
        lambda *args, **kwargs: calls.append(("verify", kwargs["phase"])),
    )
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append("tag"))
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args, **kwargs: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *args, **kwargs: calls.append("push-tag"))
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build/publish")))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)
    released = cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    )
    assert released == []
    assert calls == [
        ("progress", "a" * 40), "prepare", "backup", "gate", "tag", "backup",
        ("verify", "before tag publication"), "push-tag",
        ("verify", "after tag publication"), ("result", "demo-v1.2.3"),
        "promote", ("progress", "b" * 40),
    ]
    assert "tagged demo-v1.2.3, skipped build/publish" in capsys.readouterr().out


def test_feature_source_sequential_no_build_retains_candidate_without_main_promotion(
    monkeypatch, tmp_path, capsys,
):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: {
        "source_ref": "refs/heads/feature/demo",
        "source_commit": "a" * 40,
    })
    project = _project("demo", git_tag=True)
    workspace = transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "child", "cmru-release-demo", "a" * 40,
    )
    calls = []
    monkeypatch.setattr(
        transaction, "write_release_progress",
        lambda *_args: calls.append("progress"),
    )
    monkeypatch.setattr(transaction, "write_release_result", lambda *_args: calls.append("result"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *_args, **_kw: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *_args, **_kw: calls.append("gate"))

    def push_backup(_workspace, **kwargs):
        assert kwargs["require_feature_source"] is True
        calls.append("backup")

    monkeypatch.setattr(transaction, "push_backup_branch", push_backup)
    monkeypatch.setattr(
        transaction, "promote_workspace",
        lambda *_a, **_k: pytest.fail("feature-source release promoted origin/main"),
    )
    monkeypatch.setattr(version, "release_cmd", lambda *_args, **_kw: calls.append("tag"))
    monkeypatch.setattr(cli, "_tag_on_head", lambda *_args, **_kw: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *_args, **_kw: calls.append("push-tag"))
    monkeypatch.setattr(
        cli, "_assert_release_candidate_unchanged",
        lambda *_args, **kwargs: calls.append(kwargs["phase"]),
    )
    monkeypatch.setattr(
        cli, "_run_project_steps",
        lambda *_args, **_kw: pytest.fail("--no-build ran a build or publish step"),
    )
    monkeypatch.setattr(cli, "_git", lambda *_args, **_kw: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)

    assert cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None), no_build=True,
    ) == []
    assert "promote" not in calls
    assert "progress" in calls and "result" in calls
    assert "candidate retained on origin/cmru-release-demo" in capsys.readouterr().out


def test_sequential_publishes_before_promoting_exact_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    project = _project("demo", git_tag=True)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    calls = []
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: calls.append(("progress", args[-1])))
    monkeypatch.setattr(transaction, "write_release_result", lambda *args: calls.append(("result", args[-1])))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: calls.append("prepare"))
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args, **_kw: calls.append("gate"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: calls.append("backup"))
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: calls.append("tag"))
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args, **kwargs: "demo-v1.2.3")
    monkeypatch.setattr(cli, "_push_tags", lambda *args, **kwargs: calls.append("push-tag"))
    monkeypatch.setattr(
        cli, "_run_project_steps",
        lambda _root, _configs, _names, steps, **kwargs: calls.append(("steps", tuple(steps))),
    )
    monkeypatch.setattr(
        cli, "_assert_release_candidate_unchanged",
        lambda *args, **kwargs: calls.append(("verify", kwargs["phase"])),
    )
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: calls.append("promote"))
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)

    assert cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None),
    ) == ["demo (demo-v1.2.3)"]
    assert calls == [
        ("progress", "a" * 40), "prepare", "backup", "gate", "tag", "backup",
        # REL-05: the non-publishing build and the publishing push are separate
        # calls, so a build failure can roll the tag back.
        ("verify", "before tag publication"), "push-tag",
        ("verify", "after tag publication"),
        ("verify", "before artifact build"), ("steps", (project.build_step,)),
        ("verify", "after artifact build"), ("verify", "before publication"),
        ("steps", ("push",)), ("verify", "after publication"),
        ("result", "demo-v1.2.3"), "promote",
        ("progress", "b" * 40),
    ]
