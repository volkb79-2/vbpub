"""Exact witnesses for selected residual operational branches."""
from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import handlers, release, runner, tester_gate, transaction


def test_transaction_lists_invisible_retained_worktrees_without_mutating_them(monkeypatch, tmp_path):
    missing = tmp_path / "missing-release"
    porcelain = f"worktree {missing}\nbranch refs/heads/cmru/release/old\n"
    monkeypatch.setattr(transaction, "_git", lambda *args, **kwargs: porcelain)
    found = transaction.list_cmru_workspaces(tmp_path)
    assert found == [transaction.ReleaseWorkspace(tmp_path, missing, "cmru/release/old", "")]


def test_transaction_discard_build_workspace_refuses_paths_outside_managed_root(tmp_path):
    with pytest.raises(RuntimeError, match=r"managed \.worktrees"):
        transaction.discard_build_workspace(tmp_path, tmp_path / "elsewhere", dry_run=True)


def test_transaction_release_retention_refuses_missing_declared_artifact(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    child = repo / "child" / "demo"; child.mkdir(parents=True)
    project = repo / "demo"; project.mkdir()
    workspace = transaction.ReleaseWorkspace(repo, repo / "child", "cmru/release/x", "a" * 40)
    with pytest.raises(RuntimeError, match="declared artifact directory is missing"):
        transaction.retain_success_outputs(
            repo, workspace, {"demo": SimpleNamespace(project_root=project, artifact_dirs=("dist",))},
            {"demo": "demo-v1"}, retain_logs=False, retain_artifacts=True,
        )


def test_runner_rejects_non_table_command_before_subprocess(monkeypatch, tmp_path):
    step = SimpleNamespace(
        name="tests", step_env={}, env_command=None, required_env=[], login=None,
        registries=None, clean_dirs=[], quiet=False, bake_set_prefix=None,
        bake_set_vars=[], no_cache_env=None, commands=["not-a-table"],
    )
    monkeypatch.setattr(runner, "run_command", lambda *args, **kwargs: pytest.fail("subprocess must not run"))
    with pytest.raises(ValueError, match="Command entry must be a table"):
        runner._execute_step(step, tmp_path, tmp_path / "logs")


def test_runner_run_step_requires_one_project_and_declared_step(monkeypatch, tmp_path):
    cfg = tmp_path / "cmru.toml"
    project = SimpleNamespace(project_root=tmp_path, runner_steps={}, env={}, build_metadata=None)
    loaded = (tmp_path, {"a": project, "b": project}, (), {}, {}, "", (), {}, SimpleNamespace(), {})
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    monkeypatch.setattr("cmru.cli.apply_project_release_env", lambda *args: None)
    with pytest.raises(RuntimeError, match="project-local"):
        runner.run_step(cfg, "tests")
    loaded = (tmp_path, {"a": project}, (), {}, {}, "", (), {}, SimpleNamespace(), {})
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    with pytest.raises(ValueError, match="not declared"):
        runner.run_step(cfg, "tests")


def test_handlers_wheel_build_refuses_disappearing_builder_image(monkeypatch, tmp_path):
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "builder@sha256:abc")
    monkeypatch.setattr(handlers, "_check_build_prerequisites", lambda: None)
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _: tmp_path / ".git")
    monkeypatch.setattr(handlers, "_docker_cgroup_parent", lambda: "dev.slice")
    monkeypatch.setattr(handlers, "_host_bind_source", lambda path: str(path))
    monkeypatch.delenv(handlers._WHEEL_BUILDER_IMAGE_ENV, raising=False)
    with pytest.raises(RuntimeError, match="disappeared"):
        handlers.cmd_wheel_build(SimpleNamespace(cwd=str(tmp_path)))


def test_release_wheel_metadata_requires_version_field(tmp_path):
    wheel = tmp_path / "demo-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("demo-1.0.0.dist-info/METADATA", "Name: demo\n")
    with pytest.raises(SystemExit) as error:
        release.read_wheel_version(wheel)
    assert error.value.code == 1


def test_tester_gate_decodes_mountinfo_and_rejects_unknown_host_mapping(monkeypatch):
    mountinfo = "10 1 0:1 /host/repo /cockpit rw - bind ext4 /dev\n"
    assert tester_gate._physical_path(Path("/cockpit/cmru"), mountinfo) == Path("/host/repo/cmru")
    assert tester_gate._physical_path(Path("/outside"), mountinfo) == Path("/outside")
    monkeypatch.setattr(tester_gate.Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no mountinfo")))
    with pytest.raises(OSError, match="no mountinfo"):
        tester_gate._physical_path(Path("/cockpit/cmru"))
