"""Contract tests for version arithmetic and runner execution boundaries."""
from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import runner, version


def test_version_bumps_and_counter_ignore_malformed_counter_tags(tmp_path, monkeypatch):
    assert version.bump_version("1.2.3", "major") == "2.0.0"
    assert version.bump_version("1.2.3", "minor") == "1.3.0"
    with pytest.raises(ValueError, match="Unknown version bump"):
        version.bump_version("1.2.3", "wat")
    monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="demo-1.0.0-r2\ndemo-1.0.0-bad\n"))
    assert version._next_counter_version(tmp_path, "demo-", "1.0.0") == "1.0.0-r3"
    monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    assert version._next_counter_version(tmp_path, "demo-", "1.0.0") == "1.0.0-r1"


def test_version_external_source_requires_exact_nonempty_variable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(RuntimeError, match="requires"):
        version._external_version(project, "VERSION")
    (project / "cmru.vars").write_text("OTHER=1\nVERSION= 2.4.0 \n", encoding="utf-8")
    assert version._external_version(project, "VERSION") == "2.4.0"
    (project / "cmru.vars").write_text("VERSION=\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not define"):
        version._external_version(project, "VERSION")


def test_version_strategy_scm_and_counter_refuse_failed_git_tag(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: (calls.append(a[0]) or SimpleNamespace(returncode=1, stdout="")))
    with pytest.raises(SystemExit) as scm_error:
        version._apply_strategy_scm(tmp_path, "demo-v", "1.0.0")
    assert scm_error.value.code == 1
    with pytest.raises(SystemExit) as counter_error:
        version._apply_strategy_counter(tmp_path, "demo-v", "1.0.0")
    assert counter_error.value.code == 1
    assert any(command[:3] == ["git", "tag", "-a"] for command in calls)


def test_version_file_strategy_is_idempotent_when_version_unchanged(monkeypatch, tmp_path):
    project = tmp_path / "demo"; project.mkdir()
    version_file = project / "VERSION"; version_file.write_text("1.0.0\n")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:4] == ["diff", "--cached", "--quiet"]:
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(version.subprocess, "run", run)
    assert version._apply_strategy_file(tmp_path, "demo-v", "1.0.0", "VERSION", project) == "demo-v1.0.0"
    assert not any(command[1] == "commit" for command in calls)
    assert version._apply_strategy_scm(tmp_path, "demo-v", "1.0.1", dry_run=True) == "demo-v1.0.1"


def test_status_reports_external_and_no_tag_policies(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(version, "detect_changed_projects", lambda *a, **k: [
        ("external", SimpleNamespace(prefix="ext-v", version=SimpleNamespace(strategy="external:V"), git_tag=True), "ext-v1.0.0", "patch"),
        ("image", SimpleNamespace(prefix="img-v", version=SimpleNamespace(strategy="scm"), git_tag=False), None, "patch"),
    ])
    version.status_cmd(tmp_path, {})
    output = capsys.readouterr().out
    assert "derived by V" in output and "project-owned publication" in output


def test_runner_env_command_requires_key_value_and_sets_declared_value(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="# comment\nBUILD=2026\n"))
    runner.apply_env_command(["derive-version"], tmp_path)
    assert runner.os.environ["BUILD"] == "2026"
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="not-a-pair\n"))
    with pytest.raises(ValueError, match="KEY=VALUE"):
        runner.apply_env_command(["derive-version"], tmp_path)


def test_runner_login_multi_requires_credentials_and_invokes_each_registry(monkeypatch):
    monkeypatch.delenv("GITHUB_USERNAME", raising=False)
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")
    with pytest.raises(RuntimeError, match="GITHUB_USERNAME"):
        runner.maybe_login_multi(None, ["one", "two"])
    calls = []
    monkeypatch.setenv("GITHUB_USERNAME", "user")
    monkeypatch.setattr(runner, "_docker_login", lambda *args: calls.append(args))
    runner.maybe_login_multi(None, ["one", "two", "three"])
    assert calls == [("two", "user", "token"), ("three", "user", "token")]


def test_runner_success_evidence_requires_framework_witness():
    assert runner._success_evidence(["noise", "Ran 2 tests in 0.1s", "OK"]) == "Ran 2 tests in 0.1s; OK"
    assert runner._success_evidence(["build completed", "done"]) is None


def test_runner_run_command_streams_success_and_surfaces_quiet_failure(monkeypatch):
    class Proc:
        stdout = iter(["[ERROR] precise\n", "context\n"])
        def wait(self): return 1
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: Proc())
    log = io.StringIO()
    with pytest.raises(subprocess.CalledProcessError) as error:
        runner.run_command(["build"], Path("."), log, quiet=True)
    assert error.value.returncode == 1 and "precise" in log.getvalue()


def test_runner_execute_step_restores_environment_even_when_step_fails(monkeypatch, tmp_path):
    original = runner.os.environ.get("CMRU_TEST_SCOPED")
    def fail(*args, **kwargs):
        runner.os.environ["CMRU_TEST_SCOPED"] = "changed"
        raise RuntimeError("step failed")
    monkeypatch.setattr(runner, "_execute_step", fail)
    with pytest.raises(RuntimeError, match="step failed"):
        runner.execute_step(SimpleNamespace(), tmp_path, tmp_path / "logs", extra_env={"CMRU_TEST_SCOPED": "declared"})
    assert runner.os.environ.get("CMRU_TEST_SCOPED") == original


def test_runner_open_aggregate_log_avoids_duplicate_and_writes_separate_file(monkeypatch, tmp_path):
    local = tmp_path / "step.log"
    monkeypatch.setenv("CMRU_RUN_LOG", str(local))
    assert runner._open_aggregate_log(local, quiet=True) is None
    aggregate = tmp_path / "all.log"
    monkeypatch.setenv("CMRU_RUN_LOG", str(aggregate))
    handle = runner._open_aggregate_log(local, quiet=True)
    assert handle is not None
    handle.write("line\n"); handle.close()
    assert aggregate.read_text() == "line\n"


def test_runner_parser_main_propagates_explicit_presentation_flags(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(runner, "run_step", lambda config, step: seen.append((config, step)))
    config = tmp_path / "cmru.toml"
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _arg: config)
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda _path: (tmp_path, {"demo": SimpleNamespace(project_root=tmp_path)}, ["demo"], ["demo"], [], "project-first", {}, None, None, None),
    )
    monkeypatch.setattr("cmru.config.load_forge_config", lambda _path: SimpleNamespace(orchestration=None))
    runner.main(["--config", str(config), "--step", "tests", "--show-run-details", "--log-append"])
    assert seen[0][1] == "tests"
    assert runner.os.environ["CMRU_SHOW_RUN_DETAILS"] == "1"
    assert runner.os.environ["CMRU_LOG_APPEND"] == "1"


def test_runner_step_uses_nearest_central_config_for_project_path(monkeypatch, tmp_path):
    project_root = tmp_path / "modern-debian-tools-python-debug"
    project_root.mkdir()
    project_config = project_root / "cmru.toml"
    project_config.write_text("schema_version = 1\n", encoding="utf-8")
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text("schema_version = 1\n", encoding="utf-8")
    step = SimpleNamespace()
    project = SimpleNamespace(
        name="modern-debian-tools-python-debug",
        project_root=project_root,
        env={"RELEASE_IMAGE_FLOW": "load"},
        build_metadata=None,
        runner_steps={"build": step},
    )
    monkeypatch.setattr(
        "cmru.config.resolve_invocation_context",
        lambda *, cwd: SimpleNamespace(config_path=central),
    )
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda path: (
            tmp_path, {project.name: project}, [project.name], [], [],
            "project-first", {}, None, SimpleNamespace(), SimpleNamespace(),
        ),
    )
    monkeypatch.setattr("cmru.cli.apply_project_release_env", lambda *args: None)
    executed = []
    monkeypatch.setattr(
        runner, "execute_step",
        lambda selected, root, log_dir, **kwargs: executed.append(
            (selected, root, kwargs["extra_env"])
        ),
    )

    runner.run_step(project_config, "build")

    assert executed == [(step, project_root, project.env)]
