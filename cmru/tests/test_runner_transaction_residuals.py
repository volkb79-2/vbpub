"""Behavioral witnesses for the final runner/transaction residual branches."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import bundle, handlers, runner, tester_gate, transaction


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "source.py").write_text("x=1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "initial")
    return root


def test_runner_optional_metadata_and_registry_login_have_concrete_effects(tmp_path, monkeypatch):
    applied = []
    monkeypatch.setattr(runner, "apply_reproducible_env", lambda root: applied.append(root))
    runner.compute_build_date({}, tmp_path)
    assert applied == [tmp_path]

    parsed = runner.parse_step(
        {
            "steps": {
                "build": {
                    "commands": [{"label": "x", "argv": ["echo"]}],
                    "quiet": True,
                    "bake_set_prefix": 17,
                    "no_cache_env": 23,
                }
            }
        },
        "build",
    )
    assert parsed.bake_set_prefix == "17"
    assert parsed.no_cache_env == "23"

    login = []
    monkeypatch.setenv("LOGIN_USER", "alice")
    monkeypatch.setenv("LOGIN_TOKEN", "secret")
    monkeypatch.setattr(runner, "_docker_login", lambda registry, user, token: login.extend((registry, user, token)))
    runner.maybe_login({
        "registry": "registry.example",
        "username_env": "LOGIN_USER",
        "token_env": "LOGIN_TOKEN",
        "required": True,
    })
    assert login == ["registry.example", "alice", "secret"]


def test_runner_execute_step_derives_metadata_and_skips_null_declared_env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "compute_build_date", lambda config, root: calls.append((config, root)))
    marker = tmp_path / "env.txt"
    step = runner.StepConfig(
        name="build",
        commands=[{
            "label": "write env",
            "argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text(__import__('os').environ.get('NULL_ENV', 'absent'))"],
            "cwd": ".",
        }],
        bake_set_prefix=None,
        bake_set_vars=[],
        no_cache_env=None,
        clean_dirs=[],
        required_env=[],
        login=None,
        step_env={"NULL_ENV": None},
        env_command=None,
        registries=[],
        quiet=True,
    )
    monkeypatch.setenv("NULL_ENV", "ambient")
    runner.execute_step(step, tmp_path, tmp_path / "logs", build_metadata={"date_env": "BUILD_DATE"})
    assert calls == [({"build_metadata": {"date_env": "BUILD_DATE"}}, tmp_path)]
    assert marker.read_text(encoding="utf-8") == "ambient"
    assert os.environ["NULL_ENV"] == "ambient"


def test_transaction_retention_rejects_missing_project_root_and_untrusted_build_workspace(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/build/x", git(root, "rev-parse", "HEAD"))
    with pytest.raises(RuntimeError, match="without project_root"):
        transaction.retain_successful_build_outputs(
            root, workspace, {"demo": SimpleNamespace(artifact_dirs=["dist"])}, ["demo"],
        )

    managed = root / ".worktrees" / "not-a-worktree"
    managed.mkdir(parents=True)
    with patch.object(transaction, "_common_git_dir", side_effect=[root / ".git", tmp_path / "other.git"]):
        with pytest.raises(RuntimeError, match="not a worktree"):
            transaction.discard_build_workspace(root, managed, dry_run=True)


def test_transaction_build_retention_rejects_empty_declared_artifact(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "dist").mkdir()
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    with pytest.raises(RuntimeError, match="artifact directory is empty"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    assert (child / "logs").is_dir()
    assert not (root / "demo" / "artifacts").exists()
    transaction.remove_workspace(workspace)


def test_transaction_build_retention_refuses_destination_appearing_during_copy(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("log", encoding="utf-8")
    (child / "dist").mkdir()
    (child / "dist" / "demo.whl").write_text("wheel", encoding="utf-8")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    output_id, _, _ = transaction.build_output_id(workspace)
    original_copytree = transaction.shutil.copytree

    def copy_then_race(source, target, **kwargs):
        result = original_copytree(source, target, **kwargs)
        if Path(source).name == "logs":
            (root / "demo" / "logs" / output_id).mkdir(parents=True)
        return result

    with patch.object(transaction.shutil, "copytree", side_effect=copy_then_race):
        with pytest.raises(RuntimeError, match="destination appeared"):
            transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])
    assert (root / "demo" / "logs" / output_id).is_dir()
    assert (child / "logs" / "step.log").is_file()
    transaction.remove_workspace(workspace)


def test_transaction_release_retention_surfaces_rollback_failure(tmp_path):
    root = repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root / "release", "cmru/release/x", "a" * 40)
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "logs" / "step.log").write_text("log", encoding="utf-8")
    for directory in ("one/dist-a", "two/dist-b"):
        (child / directory).mkdir(parents=True)
        (child / directory / "artifact.whl").write_text("wheel", encoding="utf-8")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["one/dist-a", "two/dist-b"])
    original_move = transaction.shutil.move
    calls = {"artifacts": 0}

    def fail_outward_and_rollback(source, target):
        source_name = Path(source).name
        if source_name in {"dist-a", "dist-b"}:
            calls["artifacts"] += 1
            if calls["artifacts"] == 2:
                raise OSError("outward move failure")
            if calls["artifacts"] == 3:
                raise OSError("rollback move failure")
        return original_move(source, target)

    with patch.object(transaction.shutil, "move", side_effect=fail_outward_and_rollback):
        with pytest.raises(RuntimeError, match="retention rollback failed") as raised:
            transaction.retain_success_outputs(
                root, workspace, {"demo": project}, {"demo": "tag"},
                retain_logs=True, retain_artifacts=True,
            )
    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "rollback move failure"


def test_bundle_config_and_cli_fail_or_report_at_the_public_boundary(tmp_path, capsys, monkeypatch):
    config_path = tmp_path / "bundle.toml"
    config_path.write_text("placeholder", encoding="utf-8")
    base = {
        "project_root": ".",
        "archive": {"name_template": "bundle-{version}.tar.gz", "version_env": "VERSION"},
        "copy": {"files": [], "dirs": []},
    }
    with patch.object(bundle, "load_toml", return_value={**base, "wheel": None}):
        parsed = bundle.parse_config(config_path)
    assert parsed.wheel_enabled is False
    assert parsed.wheel_python_bin == "python3"
    with patch.object(bundle, "load_toml", return_value={"project_root": ".", "archive": {"name_template": "x"}, "copy": {}}):
        with pytest.raises(ValueError, match="name_template"):
            bundle.parse_config(config_path)
    with patch.object(bundle, "load_toml", return_value={
        "project_root": ".",
        "archive": {"name_template": "x-{version}.tar", "version_env": "VERSION"},
        "copy": None,
    }):
        with pytest.raises(ValueError, match=r"\[copy\]"):
            bundle.parse_config(config_path)
    with patch.object(bundle, "run_bundle", return_value=tmp_path / "dist" / "bundle.tar.gz"):
        bundle.main(["--config", str(config_path)])
    assert "Done:" in capsys.readouterr().out


def test_handlers_and_tester_gate_reject_or_report_boundary_conditions(tmp_path, monkeypatch, capsys):
    with patch.object(handlers.Path, "read_text", return_value="malformed mount line\n"):
        with pytest.raises(RuntimeError, match="no matching mount"):
            handlers._host_bind_source(tmp_path)

    monkeypatch.setenv("GITHUB_USERNAME", "alice")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")
    args = SimpleNamespace(prefix="demo", artifact_suffix=".tar.xz")
    info = {"version": "1.2.3", "asset": "demo.tar.xz", "url": "https://example/demo.tar.xz", "sha256_url": "https://example/demo.sha256"}
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *args: object())
    monkeypatch.setattr(handlers, "validate_latest_release", lambda *args, **kwargs: info)
    handlers.cmd_tarball_validate(args)
    assert "DEMO_TARBALL_SHA256_URL" in capsys.readouterr().out

    (tmp_path / "dist").mkdir()
    artifact = tmp_path / "dist" / "demo.tar.xz"
    artifact.write_bytes(b"tarball")
    monkeypatch.setenv("VERSION_FROM_ENV", "2.0.0")
    monkeypatch.setattr(handlers, "find_artifact", lambda *_args: artifact)
    monkeypatch.setattr(handlers, "publish_versioned", lambda *args, **kwargs: {"sha256": "abc"})
    handlers.cmd_tarball_publish(SimpleNamespace(
        cwd=str(tmp_path), version_file=None, version_env="VERSION_FROM_ENV",
        prefix="demo", glob="*.tar.xz", notes_env=None,
    ))
    assert "Published demo 2.0.0" in capsys.readouterr().out

    with patch.object(tester_gate, "subprocess") as process:
        process.run.return_value = SimpleNamespace(returncode=0, stdout="/repo\n")
        with pytest.raises(ValueError, match="inside"):
            tester_gate._resolve_worktree_context(tmp_path, "../outside")
    with patch.object(tester_gate, "subprocess") as process:
        process.run.return_value = SimpleNamespace(returncode=0, stdout=str(tmp_path / "other") + "\n")
        with pytest.raises(ValueError, match="inside"):
            tester_gate._resolve_worktree_context(tmp_path, "child")
    monkeypatch.setenv("CMRU_TESTER_MEMORY_SWAP", "3G")
    assert tester_gate.resolve_memory_swap(None) == "3G"
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_: (tmp_path, "."))
    monkeypatch.setattr(tester_gate, "resolve_cgroup_parent", lambda explicit: explicit or "slice")
    monkeypatch.setattr(tester_gate, "resolve_cgroup_probe_image", lambda explicit: explicit or "probe")
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda *_: (None, "probe unavailable"))
    monkeypatch.setattr(tester_gate, "build_docker_command", lambda *args, **kwargs: ["true"])
    monkeypatch.setattr(tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(SystemExit) as exit_info:
        tester_gate.main([
            "--cwd", ".", "--image", "img", "--cgroup-parent", "slice",
            "--cgroup-probe-image", "probe", "--memory", "1G", "--memory-swap", "2G",
            "--cpus", "1", "--", "true",
        ])
    assert exit_info.value.code == 0
    assert "probe unavailable" in capsys.readouterr().err

    mountinfo = "malformed\n10 1 0:1 /host/repo /cockpit rw - bind ext4 /dev\n"
    assert tester_gate._physical_path(Path("/cockpit/cmru"), mountinfo) == Path("/host/repo/cmru")


def test_bundle_copy_sources_handles_external_allowlist_paths(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "external"
    project.mkdir()
    outside.mkdir()
    (outside / "input.txt").write_text("external", encoding="utf-8")
    config_path = tmp_path / "bundle.toml"
    config_path.write_text("placeholder", encoding="utf-8")
    raw = {
        "project_root": str(project),
        "archive": {"name_template": "x-{version}.tar", "version_env": "VERSION"},
        "copy": {"files": [], "dirs": [str(outside)]},
    }
    with patch.object(bundle, "load_toml", return_value=raw):
        config = bundle.parse_config(config_path)
    config.bundle_dir.mkdir(parents=True)
    bundle.copy_sources(config)
    assert (config.bundle_dir / "external" / "input.txt").read_text(encoding="utf-8") == "external"
