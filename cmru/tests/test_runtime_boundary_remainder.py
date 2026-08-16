"""Behavior-level witnesses for remaining runtime/config/transaction branches."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, runner, transaction, version


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "source.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def test_config_rejects_malformed_wheels_variants_and_secret_documents(tmp_path):
    installer = {"install_dir_system": "/opt/demo", "install_dir_user": "demo"}
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**installer, "wheels": "not-an-array"})
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**installer, "wheels": [{"path": "x"}]})
    with pytest.raises(SystemExit):
        config._parse_variants("demo", {"variants": "not-an-array"})
    with pytest.raises(SystemExit):
        config._parse_variants("demo", {"variants": [{"name": "../unsafe"}]})

    secret = tmp_path / "cmru.secret.toml"
    secret.write_text("[github]\ntoken='x'\nextra='nope'\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config._read_secret_document(secret)
    secret.write_text("[github]\ntoken='x'\n", encoding="utf-8")
    assert config._read_secret_document(secret)["github"]["token"] == "x"


def test_config_parser_refuses_wrong_project_shapes_and_accepts_scalar_env():
    assert config._scalar_env({"COUNT": 2, "FLAG": True}, "env") == {
        "COUNT": "2", "FLAG": "True"
    }
    with pytest.raises(SystemExit):
        config._targets([])
    with pytest.raises(SystemExit):
        config._github([])
    with pytest.raises(SystemExit):
        config._parse_project_document(Path("does-not-exist.toml"))


def test_runner_execute_step_scopes_declared_environment_and_removes_clean_dirs(tmp_path, monkeypatch):
    stale = tmp_path / "cache"
    stale.mkdir()
    (stale / "old").write_text("old", encoding="utf-8")
    monkeypatch.setenv("CMRU_TEST_STALE", "ambient")
    step = runner.StepConfig(
        "boundary",
        [{"label": "observe", "argv": [sys.executable, "-c", "import os; print(os.getenv('CMRU_TEST_STALE'))"], "cwd": "."}],
        None, [], None, ["cache"], [], None, {}, None, [], False,
    )
    runner.execute_step(step, tmp_path, tmp_path / "logs", extra_env={"CMRU_TEST_STALE": "declared"})
    assert not stale.exists()
    assert "declared" in (tmp_path / "logs" / "boundary.log").read_text(encoding="utf-8")
    assert runner.os.environ["CMRU_TEST_STALE"] == "ambient"


def test_runner_rejects_malformed_runtime_command_after_log_creation(tmp_path):
    step = runner.StepConfig(
        "bad", [{"label": "missing-argv", "cwd": "."}],
        None, [], None, [], [], None, {}, None, [], False,
    )
    with pytest.raises(ValueError, match="argv"):
        runner.execute_step(step, tmp_path, tmp_path / "logs")
    assert (tmp_path / "logs" / "bad.log").is_file()


def test_transaction_retention_refuses_missing_sources_and_delete_requires_authorized_manifest(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.create_workspace(root, base=_git(root, "rev-parse", "HEAD"), purpose="build")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    (workspace.path / "demo" / "logs").mkdir()
    with pytest.raises(RuntimeError, match="artifact directory"):
        transaction.retain_successful_build_outputs(root, workspace, {"demo": project}, ["demo"])

    output_id = "20260816T000000Z_" + _git(root, "rev-parse", "HEAD")
    artifacts = root / "demo" / "artifacts" / output_id
    logs = root / "demo" / "logs" / output_id
    artifacts.mkdir(parents=True)
    logs.mkdir(parents=True)
    (artifacts / "build.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not authorize"):
        transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=False)
    transaction.remove_workspace(workspace)


def test_transaction_release_result_and_progress_records_fail_closed_on_bad_json(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.ReleaseWorkspace(root, root, "cmru/release/test", "a" * 40)
    scope = transaction._scope_dir(root)
    scope.mkdir()
    (scope / "test.results.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.write_release_result(root, workspace, "demo", "tag")
    (scope / "test.results.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read release result"):
        transaction.read_release_results(root, workspace)
    (scope / "test.progress").write_text("\n", encoding="utf-8")
    assert transaction.read_release_progress(root, workspace) is None


def test_transaction_discard_build_workspace_validates_managed_path_and_dry_run(tmp_path):
    root = _repo(tmp_path)
    workspace = transaction.create_workspace(root, base=_git(root, "rev-parse", "HEAD"), purpose="build")
    assert transaction.discard_build_workspace(root, workspace.path, dry_run=True) == workspace
    with pytest.raises(RuntimeError, match="managed .worktrees"):
        transaction.discard_build_workspace(root, tmp_path / "outside", dry_run=True)
    transaction.remove_workspace(workspace)


def test_version_tag_strategies_report_tag_failures_without_claiming_success(tmp_path):
    failed = SimpleNamespace(returncode=2, stdout="")
    with patch.object(version.subprocess, "run", return_value=failed):
        with pytest.raises(SystemExit) as scm_error:
            version._apply_strategy_scm(tmp_path, "demo-v", "1.0.0")
        assert scm_error.value.code == 1
    with patch.object(version.subprocess, "run", side_effect=[SimpleNamespace(returncode=0, stdout=""), failed]):
        with pytest.raises(SystemExit) as counter_error:
            version._apply_strategy_counter(tmp_path, "demo-v", "1.0.0")
        assert counter_error.value.code == 1


def test_version_status_reports_external_and_no_tag_contracts(tmp_path, capsys):
    external = SimpleNamespace(prefix="demo-v", version=SimpleNamespace(strategy="external:VERSION"), git_tag=True)
    no_tag = SimpleNamespace(prefix="plain-v", version=SimpleNamespace(strategy="none"), git_tag=False)
    with patch.object(version, "detect_changed_projects", return_value=[
        ("demo", external, None, "patch"), ("plain", no_tag, None, "patch")
    ]):
        version.status_cmd(tmp_path, {"demo": external, "plain": no_tag})
    output = capsys.readouterr().out
    assert "derived by VERSION" in output
    assert "project-owned publication, no git tag" in output
