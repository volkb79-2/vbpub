import pytest

from cmru import cli, transaction, version


def test_sequential_tagged_release_fails_closed_when_no_tag_reaches_head(monkeypatch, tmp_path):
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", git_tag=True, build_step="build")
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(transaction, "start_untagged_publication_record", lambda *a: None)
    monkeypatch.setattr(transaction, "mark_untagged_publication_started", lambda *a: None)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args, **_kw: None)
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(cli, "_assert_release_gate_source_matches_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(version, "release_cmd", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_tag_on_head", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_project_steps", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build/publish")))
    with pytest.raises(RuntimeError, match="no tag ended up on HEAD"):
        cli._release_projects_sequentially(
            tmp_path, {"demo": project}, workspace, ["demo"],
            github_config=cli.GitHubConfig("o", "r", "t", "user"),
            env_config=cli.ReleaseEnvConfig({}, None),
        )


def test_untagged_publication_is_recorded_durably_before_it_can_publish(monkeypatch, tmp_path):
    """KI-98: `cmru abandon` trusts the record, so it must exist before the publish step."""
    order = []
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", git_tag=False, build_step="build")
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: order.append("progress"))
    monkeypatch.setattr(
        transaction, "start_untagged_publication_record", lambda *a: order.append("start"),
    )
    monkeypatch.setattr(
        transaction, "mark_untagged_publication_started", lambda *a: order.append(f"mark:{a[-1]}"),
    )
    monkeypatch.setattr(transaction, "write_release_result", lambda *a: order.append("result"))
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates", lambda *args, **_kw: None)
    monkeypatch.setattr(cli, "_run_release_gates_on_exact_head", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(cli, "_assert_release_candidate_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(cli, "_run_untagged_project", lambda *a, **k: order.append("publish"))
    cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"],
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None),
    )
    # The record is created before the progress seed (the seed's file is how a
    # resumed older transaction is told apart), and marked before it can publish.
    assert order == ["start", "progress", "mark:demo", "publish", "result", "progress"]


def test_untagged_no_build_marks_nothing_and_starts_the_record_once(monkeypatch, tmp_path):
    order = []
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", git_tag=False, build_step="build")
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "child", "cmru/release/x", "a" * 40)
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(transaction, "write_release_progress", lambda *args: None)
    monkeypatch.setattr(
        transaction, "start_untagged_publication_record", lambda *a: order.append("start"),
    )
    monkeypatch.setattr(
        transaction, "mark_untagged_publication_started", lambda *a: order.append("mark"),
    )
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *args: None)
    monkeypatch.setattr(cli, "_prepare_release_projects", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_run_release_gates_on_exact_head", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(transaction, "promote_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_git", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr(
        cli, "_run_untagged_project", lambda *a, **k: pytest.fail("--no-build published"),
    )
    cli._release_projects_sequentially(
        tmp_path, {"demo": project}, workspace, ["demo"], no_build=True,
        github_config=cli.GitHubConfig("o", "r", "t", "user"),
        env_config=cli.ReleaseEnvConfig({}, None),
    )
    assert order == ["start"]
