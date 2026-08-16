"""High-signal residual contracts across config, transaction, and runner."""
from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, runner, transaction


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "x@y.invalid"); git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_doc():
    return '''schema_version=1
[github]
owner="acme"
repo="vbpub"
owner_type="org"
[targets]
host="github"
registry=["ghcr.io"]
[project]
id="demo"
description="demo"
prefix="demo-v"
artifacts=["wheel"]
[project.version]
strategy="scm"
bump="patch"
[project.release]
git_tag=true
build_step="build"
artifact_dirs=["dist"]
[steps.run-tests]
quiet=true
commands=[{label="tests",argv=["echo"],cwd="."}]
[steps.build]
quiet=true
commands=[{label="build",argv=["echo"],cwd="."}]
[steps.push]
quiet=true
commands=[{label="push",argv=["echo"],cwd="."}]
'''


def test_config_remaining_project_and_secret_policies(tmp_path, capsys):
    secret = tmp_path / "cmru.secret.toml"; secret.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit): config._read_secret_document(secret)
    path = tmp_path / "cmru.toml"
    cases = (
        (project_doc() + "build_metadata={unsupported=\"x\"}\n", "build_metadata"),
        (project_doc() + "project_metadata=[]\n", "project_metadata"),
        (project_doc().replace('id="demo"', 'id="Bad"'), "project.id"),
    )
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit): config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out


def test_transaction_result_write_and_build_discard_refuse_invalid_state(tmp_path):
    root = repo(tmp_path); ws = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    scope = transaction._scope_dir(root); scope.mkdir(); (scope / "x.results.json").write_text("bad")
    with pytest.raises(RuntimeError, match="cannot read release result"):
        transaction.write_release_result(root, ws, "demo", "tag")
    with pytest.raises(RuntimeError, match="managed .worktrees"):
        transaction.discard_build_workspace(root, tmp_path / "outside", dry_run=False)


def test_transaction_build_retention_rejects_collision_and_rolls_back_destination(tmp_path):
    root = repo(tmp_path); ws = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = ws.path / "demo"; (child / "logs").mkdir(parents=True); (child / "dist").mkdir(); (child / "dist" / "x.whl").write_bytes(b"x")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    output_id, _, _ = transaction.build_output_id(ws); (root / "demo" / "artifacts" / output_id).mkdir(parents=True)
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.retain_successful_build_outputs(root, ws, {"demo": project}, ["demo"])
    (root / "demo" / "artifacts" / output_id).rmdir()
    original_replace = Path.replace
    def fail_second(self, target):
        if self.name == "artifacts" and Path(target).parent.name == "artifacts": raise OSError("artifact destination")
        return original_replace(self, target)
    with patch.object(Path, "replace", new=fail_second), pytest.raises(OSError, match="artifact destination"):
        transaction.retain_successful_build_outputs(root, ws, {"demo": project}, ["demo"])
    assert not (root / "demo" / "logs" / output_id).exists()
    assert (child / "logs").is_dir()
    transaction.remove_workspace(ws)


def test_transaction_release_retention_refuses_log_destination_collision(tmp_path):
    root = repo(tmp_path); ws = transaction.ReleaseWorkspace(root, root, "cmru/release/x", "a" * 40)
    child = ws.path / "demo"; (child / "logs").mkdir(parents=True); (child / "logs" / "step.log").write_text("x")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    target_log = root / "demo" / "logs" / "cmru-release" / "tag"; target_log.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="retained log destination"):
        transaction.retain_success_outputs(root, ws, {"demo": project}, {"demo": "tag"}, retain_logs=True, retain_artifacts=False)
    assert target_log.is_dir()
    assert (child / "logs" / "step.log").is_file()


def test_runner_validates_and_scopes_runtime_environment(tmp_path, capsys, monkeypatch):
    runner.log_error("failure")
    assert "failure" in capsys.readouterr().err
    assert runner.resolve_path(tmp_path, "/absolute") == Path("/absolute")
    with pytest.raises(ValueError, match="not found"):
        runner.parse_step({"steps": {"build": {"commands": [{"argv": ["echo"]}], "quiet": True}}}, "missing")
    with pytest.raises(ValueError, match="build_metadata"):
        runner.compute_build_date({"build_metadata": "bad"}, tmp_path)
    monkeypatch.setenv("C", "ambient")
    step = runner.StepConfig("env", [{"label": "x", "argv": [sys.executable, "-c", "print('ok')"], "cwd": "."}], None, [], None, [], [], None, {}, None, [], False)
    runner.execute_step(step, tmp_path, tmp_path / "logs", extra_env={"C": None})
    assert os.getenv("C") == "ambient"


def test_runner_docker_login_uses_stdin_and_main_flags(monkeypatch):
    calls = []
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    runner._docker_login("registry", "user", "secret")
    assert calls[0][0][0] == ["docker", "login", "registry", "-u", "user", "--password-stdin"]
    assert calls[0][1]["input"] == "secret\n"
    with patch.object(runner, "run_step") as run:
        runner.main(["--config", "cmru.toml", "--step", "build", "--show-run-details", "--log-append"])
    run.assert_called_once()
