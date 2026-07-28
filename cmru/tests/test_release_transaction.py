"""Behavioural contract tests for isolated, source-first release transactions."""
from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, transaction
from cmru import tester_gate


def _project(name: str, *, paths: list[str] | None = None, steps=None):
    return SimpleNamespace(
        name=name,
        cwd=name,
        paths=paths or [name],
        steps=steps or {},
    )


def test_release_input_paths_only_include_selected_product_and_control_plane():
    configs = {
        "alpha": _project("alpha", paths=["alpha", "shared"]),
        "beta": _project("beta"),
    }

    assert cli._release_input_paths(configs, ["alpha", "beta"], "alpha") == [
        "cmru.py", "cmru", "cmru.toml", "alpha", "shared",
    ]


def test_child_args_replaces_absolute_config_with_snapshot_relative_path(tmp_path):
    config = tmp_path / "nested" / "cmru.toml"
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")

    assert cli._child_release_args(
        ["--project", "alpha", "--config", str(config)], config, tmp_path,
    ) == ["--project", "alpha", "--config", "nested/cmru.toml"]


def test_child_args_removes_parent_only_resume_option(tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text("", encoding="utf-8")

    assert cli._child_release_args(
        ["--resume", "/tmp/retained", "--project", "alpha"], config, tmp_path,
    ) == ["--project", "alpha", "--config", "cmru.toml"]


def test_required_gate_rejects_project_without_run_tests(tmp_path):
    with pytest.raises(RuntimeError, match="no release gate"):
        cli._run_release_gates(tmp_path, {"alpha": _project("alpha")}, ["alpha"])


def test_required_gate_runs_declared_command(tmp_path, monkeypatch):
    project = _project("alpha", steps={"run-tests": [object()]})
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        cli, "run_project_step",
        lambda _project, step, root, _logs: calls.append((step, root)),
    )

    cli._run_release_gates(tmp_path, {"alpha": project}, ["alpha"])

    assert calls == [("run-tests", tmp_path)]


def test_prepare_commits_only_declared_generated_paths(tmp_path, monkeypatch):
    project = _project("alpha")
    project.commit_generated = ("generated",)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda _root: ["alpha/generated/result.txt"])
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))

    assert cli._commit_prepared_generated(tmp_path, project) is True
    assert calls[0][0] == ["git", "add", "-A", "--", "alpha/generated/result.txt"]
    assert calls[1][0][-1] == "chore(alpha): prepare release inputs"


def test_prepare_rejects_undeclared_write(tmp_path, monkeypatch):
    project = _project("alpha")
    project.commit_generated = ("generated",)
    monkeypatch.setattr(cli, "_worktree_changed_paths", lambda _root: ["alpha/not-allowed.txt"])

    with pytest.raises(RuntimeError, match="undeclared paths"):
        cli._commit_prepared_generated(tmp_path, project)


def test_parent_release_launches_isolated_child_and_never_runs_in_caller(
    tmp_path, monkeypatch
):
    config = tmp_path / "cmru.toml"
    config.write_text(
        """
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[orchestration]
project_order = ["alpha"]
[project.alpha]
prefix = "alpha-v"
cwd = "alpha"
""",
        encoding="utf-8",
    )
    project = _project("alpha")
    loaded = (tmp_path, {"alpha": project}, ["alpha"], ["alpha"], [], "project-first", {},
              SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "a" * 40)
    calls: list[object] = []

    monkeypatch.setattr(cli, "load_config", lambda _path: loaded)
    monkeypatch.setattr(cli, "apply_release_env", lambda *_args: None)
    monkeypatch.setattr(transaction, "assert_paths_clean", lambda root, paths: calls.append((root, paths)))
    monkeypatch.setattr(transaction, "release_lock", lambda _root: nullcontext())
    monkeypatch.setattr(transaction, "fetch_origin_main", lambda _root: "a" * 40)
    monkeypatch.setattr(transaction, "assert_local_main_not_ahead", lambda _root: 0)
    monkeypatch.setattr(transaction, "create_workspace", lambda _root, *, base: workspace)
    monkeypatch.setattr(transaction, "copy_secret_overlay", lambda *_args: calls.append("secret"))
    monkeypatch.setattr(transaction, "run_child", lambda _workspace, args: calls.append(list(args)) or 0)
    monkeypatch.setattr(transaction, "remove_workspace", lambda _workspace: calls.append("removed"))

    with pytest.raises(SystemExit) as exc:
        cli.main(["release", "--config", str(config), "--project", "alpha"])

    assert exc.value.code == 0
    assert (tmp_path, ["cmru.py", "cmru", "cmru.toml", "alpha"]) in calls
    assert ["--project", "alpha", "--config", "cmru.toml"] in calls
    assert "removed" in calls


def test_local_main_ahead_aborts_before_creating_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "local_main_divergence", lambda _root: (2, 0))

    with pytest.raises(RuntimeError, match="Local main is 2 commit\\(s\\) ahead"):
        transaction.assert_local_main_not_ahead(tmp_path)


def test_local_main_behind_is_reported_but_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "local_main_divergence", lambda _root: (0, 3))

    assert transaction.assert_local_main_not_ahead(tmp_path) == 3


def test_tester_gate_maps_the_deepest_cockpit_mount_to_docker_host_path(tmp_path):
    repo = Path("/workspaces/vbpub/.worktrees/release")
    mountinfo = (
        "1 0 0:1 / / rw - overlay overlay rw\n"
        "2 1 0:2 /home/vb/vbpub /workspaces/vbpub rw - ext4 /dev/vda rw\n"
    )

    assert tester_gate._physical_path(repo, mountinfo) == Path("/home/vb/vbpub/.worktrees/release")


def test_tester_gate_uses_explicit_container_workdir_and_no_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda _path: Path("/host/repo"))

    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q"],
    )

    assert argv == [
        "docker", "run", "--rm", "--mount", "type=bind,src=/host/repo,dst=/worktree",
        "--workdir", "/worktree/cmru", "tester-unified:local",
        "/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q",
    ]


def test_tester_gate_rejects_paths_outside_the_worktree(tmp_path):
    with pytest.raises(ValueError, match="relative path"):
        tester_gate.build_docker_command(tmp_path, "../ciu", ["true"])


def test_run_child_marks_process_as_transaction_child(tmp_path, monkeypatch):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "b" * 40)
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        observed["env"] = kwargs["env"]
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    assert transaction.run_child(workspace, ["--project", "alpha"]) == 17
    assert observed["command"][-3:] == ["release", "--project", "alpha"]
    assert observed["cwd"] == workspace.path
    assert observed["env"][transaction.CHILD_ENV] == "1"
    assert observed["env"][transaction.BRANCH_ENV] == workspace.branch


def test_promote_workspace_fast_forwards_remote_main(tmp_path, monkeypatch):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "release", "cmru/release/x", "c" * 40)
    calls = []
    monkeypatch.setattr(transaction.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))

    transaction.promote_workspace(workspace)

    assert calls[0][0] == ["git", "push", "origin", "HEAD:refs/heads/main"]
    assert calls[0][1]["cwd"] == workspace.path


def test_resume_rejects_worktree_from_another_repository(tmp_path, monkeypatch):
    retained = tmp_path / "retained"
    retained.mkdir()
    monkeypatch.setattr(transaction, "_common_git_dir", lambda path: Path("/a") if path == tmp_path else Path("/b"))

    with pytest.raises(RuntimeError, match="not a worktree"):
        transaction.resume_workspace(tmp_path, retained)
