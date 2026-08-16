"""Behavioural contracts for CMRU's core config, runner and transaction seams.

External GitHub/Docker operations are stopped at their subprocess boundary;
filesystem artifacts and small local subprocesses remain real so the tests
assert the contract rather than merely exercising helper calls.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, config, runner, transaction


def test_cli_main_rejects_unknown_verb_with_machine_exit_code(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["not-a-cmru-verb"])
    assert exc.value.code == 2
    assert "Unknown verb" in capsys.readouterr().err


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"strategy": "scm", "bump": "major"}, "bump"),
        ({"strategy": "scm", "paths": "not-a-list"}, "paths"),
    ],
)
def test_config_version_schema_rejects_ambiguous_values(raw, message):
    with pytest.raises(SystemExit, match="2"):
        config._parse_version(raw, "demo")


def test_config_scalar_env_rejects_nested_value_instead_of_stringifying_it(capsys):
    with pytest.raises(SystemExit) as exc:
        config._scalar_env({"CMRU_MEMORY": {"value": "3g"}}, "env")
    assert exc.value.code == 2
    assert "scalar values" in capsys.readouterr().out


def test_config_release_policy_rejects_unknown_artifacts_and_invalid_tag_policy():
    with pytest.raises(ValueError, match="unknown artifact"):
        cli._parse_release_policy(
            {"artifacts": ["mystery"], "release": {"git_tag": True}},
            "demo", cli.VersionSpec(),
        )
    with pytest.raises(ValueError, match="git_tag"):
        cli._parse_release_policy(
            {"artifacts": [], "release": {"git_tag": "yes"}},
            "demo", cli.VersionSpec(),
        )


def test_config_project_override_wins_over_estate_default(tmp_path):
    # Reuse the checked schema fixture, but include a root default and a
    # project-owned value.  load_config must preserve the explicit override.
    orchestration = tmp_path / "cmru.orchestration.toml"
    orchestration.write_text(
        """schema_version = 1
[orchestration]
project_order = [\"alpha\"]
default_projects = [\"alpha\"]
default_steps = [\"run-tests\", \"build\", \"push\"]
execution_mode = \"project-first\"
[orchestration.project.alpha]
config = \"alpha/cmru.toml\"
depends_on = []
[cleanup]
release_tag_prefixes = [\"*\"]
keep_release_tags = []
ghcr_packages = [\"*\"]
ghcr_delete_packages = []
""", encoding="utf-8",
    )
    project_dir = tmp_path / "alpha"
    project_dir.mkdir()
    (project_dir / "cmru.toml").write_text(
        """schema_version = 1
[github]
owner = \"octocat\"
repo = \"demo\"
owner_type = \"user\"
[targets]
host = \"github\"
registry = []
[env]
CMRU_TESTER_CPUS = \"2\"
[project]
id = \"alpha\"
description = \"alpha\"
prefix = \"alpha-v\"
artifacts = [\"wheel\"]
[project.version]
strategy = \"scm\"
bump = \"conventional\"
[project.release]
git_tag = true
build_step = \"build\"
[steps.run-tests]
quiet = true
commands = [{ label = \"test\", argv = [\"true\"], cwd = \".\" }]
[steps.build]
quiet = true
commands = [{ label = \"build\", argv = [\"true\"], cwd = \".\" }]
[steps.push]
quiet = true
commands = [{ label = \"push\", argv = [\"true\"], cwd = \".\" }]
""", encoding="utf-8",
    )
    orchestration.write_text(
        orchestration.read_text(encoding="utf-8").replace(
            'execution_mode = "project-first"\n',
            'execution_mode = "project-first"\n[orchestration.defaults.env]\nCMRU_TESTER_CPUS = "1"\n',
        ),
        encoding="utf-8",
    )
    _root, projects, *_ = cli.load_config(orchestration)
    assert projects["alpha"].env["CMRU_TESTER_CPUS"] == "2"


def test_runner_parse_step_rejects_each_non_list_control():
    controls = ["commands", "bake_set_vars", "clean_dirs", "required_env"]
    for key in controls:
        raw = {"steps": {"run-tests": {"commands": [{"argv": ["true"], "cwd": "."}], "quiet": True}}}
        raw["steps"]["run-tests"][key] = "wrong"
        with pytest.raises(ValueError, match=key):
            runner.parse_step(raw, "run-tests")


def test_runner_env_command_accepts_comments_and_restores_scoped_environment(tmp_path):
    result = SimpleNamespace(stdout="# generated\nNEW_VALUE=from-child\n", returncode=0)
    original = os.environ.get("NEW_VALUE")
    with contextlib.ExitStack() as stack:
        stack.callback(lambda: os.environ.pop("NEW_VALUE", None) if original is None else os.environ.__setitem__("NEW_VALUE", original))
        # apply_env_command is intentionally a real boundary parser; only its
        # external command is replaced.
        monkey = pytest.MonkeyPatch()
        stack.callback(monkey.undo)
        monkey.setattr(runner.subprocess, "run", lambda *args, **kwargs: result)
        runner.apply_env_command(["emit-env"], tmp_path)
        assert os.environ["NEW_VALUE"] == "from-child"


def test_runner_login_requires_token_and_username_before_external_login(monkeypatch):
    monkeypatch.delenv("TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TOKEN"):
        runner.maybe_login({"registry": "registry.test", "username_env": "USER", "token_env": "TOKEN", "required": True})
    monkeypatch.setenv("TOKEN", "secret")
    monkeypatch.delenv("USER", raising=False)
    with pytest.raises(RuntimeError, match="USER"):
        runner.maybe_login({"registry": "registry.test", "username_env": "USER", "token_env": "TOKEN", "required": True})


def test_runner_success_evidence_requires_a_known_test_framework_fact():
    assert runner._success_evidence(["progress", "Ran 2 tests in 0.1s", "OK"]) == "Ran 2 tests in 0.1s; OK"
    assert runner._success_evidence(["build completed", "all good"]) is None


def test_runner_compute_build_date_is_commit_derived(monkeypatch, tmp_path):
    values = {"log -1 --format=%ct": "1700000000", "rev-parse HEAD": "abc123"}
    monkeypatch.setattr(runner, "_git_out", lambda _root, *args: values[" ".join(args)])
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.delenv("BUILD_DATE", raising=False)
    runner.compute_build_date({"build_metadata": {"date_format": "%Y"}}, tmp_path)
    assert os.environ["BUILD_DATE"] == "2023"
    assert os.environ["OCI_REVISION"] == "abc123"


def test_transaction_scope_and_results_are_durable_and_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    transaction.write_release_scope(tmp_path, workspace, ["zeta", "alpha"])
    assert transaction.read_release_scope(tmp_path, workspace) == ["alpha", "zeta"]
    transaction.write_release_result(tmp_path, workspace, "zeta", "v1")
    transaction.write_release_result(tmp_path, workspace, "alpha", "v2")
    assert transaction.read_release_results(tmp_path, workspace) == {"alpha": "v2", "zeta": "v1"}
    transaction.forget_release_scope(tmp_path, workspace)
    assert transaction.read_release_scope(tmp_path, workspace) is None
    assert transaction.read_release_results(tmp_path, workspace) == {}


def test_transaction_rejects_corrupt_durable_result_record(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    scope_dir = tmp_path / "cmru-release-scopes"
    scope_dir.mkdir()
    (scope_dir / "id.results.json").write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.read_release_results(tmp_path, workspace)


def test_transaction_read_progress_treats_io_failure_as_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(transaction, "_common_git_dir", lambda _root: tmp_path)
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    scope = tmp_path / "cmru-release-scopes"
    scope.mkdir()
    progress = scope / "id.progress"
    progress.write_text("checkpoint", encoding="utf-8")
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("gone")) if self == progress else original(self, *args, **kwargs))
    assert transaction.read_release_progress(tmp_path, workspace) is None


def test_transaction_build_output_id_rejects_non_commit_timestamp(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/build/id", "a" * 40)
    monkeypatch.setattr(transaction, "_git", lambda _root, *args, **kwargs: "a" * 40 if "rev-parse" in args else "not-a-time")
    with pytest.raises(RuntimeError, match="timestamp"):
        transaction.build_output_id(workspace)


def test_transaction_revert_reports_noop_without_external_mutation(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    monkeypatch.setattr(transaction, "_git", lambda _root, *args, **kwargs: "a" * 40)
    calls = []
    monkeypatch.setattr(transaction.subprocess, "run", lambda *args, **kwargs: calls.append(args[0]))
    assert transaction.revert_promotion(workspace) == transaction.RevertResult(ok=True, reverted=False)
    assert calls == []


def test_transaction_revert_aborts_failed_revert_and_reports_manual_recovery(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    monkeypatch.setattr(transaction, "_git", lambda _root, *args, **kwargs: "b" * 40)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    assert transaction.revert_promotion(workspace).ok is False
    assert any(argv[:3] == ["git", "revert", "--abort"] for argv in calls)


def test_transaction_run_child_propagates_child_exit_and_transaction_identity(monkeypatch, tmp_path):
    workspace = transaction.ReleaseWorkspace(tmp_path, tmp_path / "workspace", "cmru/release/id", "a" * 40)
    monkeypatch.setenv("CMRU_BIN", "/opt/cmru")
    seen = {}
    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=17)
    monkeypatch.setattr(transaction.subprocess, "run", fake_run)
    assert transaction.run_child(workspace, ["--project", "alpha"], verb="build") == 17
    assert seen["argv"] == ["/opt/cmru", "build", "--_transaction-child", "--project", "alpha"]
    assert seen["env"][transaction.CHILD_ENV] == "1"
    assert seen["env"][transaction.BRANCH_ENV] == workspace.branch
