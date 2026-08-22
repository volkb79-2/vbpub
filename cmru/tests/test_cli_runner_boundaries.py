"""Behavioural boundary coverage for the public CMRU orchestration seams.

These tests deliberately observe errors, emitted diagnostics, and durable log
artifacts.  They do not substitute a mock for the command runner: commands
which are exercised here are tiny local subprocesses, while Docker/GitHub
boundaries are mocked only at the point where they would cross the host.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, dependencies, runner, tester_gate
from cmru.standards import assess_projects


@pytest.mark.parametrize(
    "raw, message",
    [("", "empty"), ("0s", "positive"), ("4fortnights", "Unknown duration"),
     ("3x", "Unknown duration")],
)
def test_duration_rejects_malformed_or_non_positive_values(raw, message):
    with pytest.raises(ValueError, match=message):
        cli.parse_duration(raw)


def test_duration_accumulates_compound_units_and_ignores_spaces():
    assert cli.parse_duration("1h 2min 3s").total_seconds() == 3723


def test_parse_commands_reports_each_invalid_public_shape(tmp_path):
    cases = [
        ([], "at least one command"),
        (["shell"], "command 1 must be a table"),
        ([{}], "missing label"),
        ([{"label": "x", "argv": [1], "cwd": "."}], "argv list"),
        ([{"label": "x", "argv": ["true"]}], "missing cwd"),
    ]
    for raw, expected in cases:
        with pytest.raises(ValueError, match=expected):
            cli.parse_commands(tmp_path / "cmru.toml", tmp_path, "run-tests", raw)


def test_http_json_boundary_preserves_api_error_body(monkeypatch):
    monkeypatch.setattr(cli, "http_request", lambda *_: (503, '{"message":"busy"}', {}))
    with pytest.raises(RuntimeError, match=r"GitHub API error 503:.*busy"):
        cli.load_json("https://api.invalid", "token")


def test_load_json_empty_body_is_an_empty_collection(monkeypatch):
    headers = {"X-RateLimit-Remaining": "0"}
    monkeypatch.setattr(cli, "http_request", lambda *_: (200, "  ", headers))
    assert cli.load_json("https://api.invalid", "token") == ([], headers)


def test_dependency_report_detects_unknown_self_order_and_alias_collision(tmp_path):
    a = SimpleNamespace(project_root=tmp_path / "a", scm_dist="shared_pkg")
    b = SimpleNamespace(project_root=tmp_path / "b", scm_dist="shared-pkg")
    a.project_root.mkdir(); b.project_root.mkdir()
    report = dependencies.build_report(
        repo_root=tmp_path,
        project_order=["b", "a"],
        declared={"a": ["a", "missing"], "b": []},
        projects={"a": a, "b": b},
    )
    assert not report.ok
    assert any("share first-party" in item for item in report.errors)
    assert any("depends on itself" in item for item in report.errors)
    assert any("unknown dependency" in item for item in report.errors)


def test_dependency_artifact_unknown_and_self_inputs_are_reported(tmp_path):
    provider = SimpleNamespace(project_root=tmp_path / "provider", scm_dist=None)
    consumer = SimpleNamespace(project_root=tmp_path / "consumer", scm_dist=None)
    provider.project_root.mkdir(); (consumer.project_root / "pip").mkdir(parents=True)
    (consumer.project_root / "pip" / "wheels.list").write_text(
        "provider\nconsumer[extra]\nnot-a-project # comment\n", encoding="utf-8"
    )
    report = dependencies.build_report(
        repo_root=tmp_path,
        project_order=["provider", "consumer"],
        declared={"provider": [], "consumer": []},
        projects={"provider": provider, "consumer": consumer},
    )
    assert any("contains itself" in item for item in report.errors)
    assert any("unknown first-party" in item for item in report.errors)
    assert report.artifact_inputs["consumer"] == ("provider",)
    assert "PREFLIGHT: FAIL" in dependencies.render_text(report)


def test_dependency_writer_refuses_malformed_or_missing_orchestration_markers(tmp_path):
    report = dependencies.DependencyReport(("demo",), {"demo": ()}, {}, (), ())
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("# BEGIN CMRU GENERATED DEPENDENCY GRAPH\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        dependencies.write_comment_block(malformed, report)
    missing = tmp_path / "missing.toml"
    missing.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        dependencies.write_comment_block(missing, report)


def test_runner_dynamic_environment_rejects_non_key_value_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="GOOD=value\nnot-a-setting\n"),
    )
    with pytest.raises(ValueError, match="KEY=VALUE"):
        runner.apply_env_command(["emit-env"], tmp_path)


def test_runner_dynamic_environment_rejects_empty_key(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="=value\n"),
    )
    with pytest.raises(ValueError, match="empty key"):
        runner.apply_env_command(["emit-env"], tmp_path)


def test_runner_required_environment_names_all_missing_values():
    with pytest.raises(RuntimeError, match="A, B"):
        runner.ensure_required_env(["A", "B"])


def test_runner_parse_step_rejects_missing_sections_and_non_boolean_quiet():
    with pytest.raises(ValueError, match=r"\[steps\] section"):
        runner.parse_step({}, "run-tests")
    with pytest.raises(ValueError, match="explicitly true or false"):
        runner.parse_step({"steps": {"run-tests": {"commands": [{}]}}}, "run-tests")


def test_runner_nonzero_command_preserves_log_and_raises(tmp_path):
    log = tmp_path / "command.log"
    with log.open("w", encoding="utf-8") as handle:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            runner.run_command(
                [sys.executable, "-c", "print('bad'); raise SystemExit(7)"],
                tmp_path, handle, log_path=log,
            )
    assert caught.value.returncode == 7
    assert "bad" in log.read_text(encoding="utf-8")


def test_runner_build_date_requires_a_commit_when_metadata_requested(tmp_path, monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(runner, "apply_reproducible_env", lambda _root: None)
    with pytest.raises(RuntimeError, match="derive BUILD_DATE"):
        runner.compute_build_date({"build_metadata": {"date_env": "BUILD_DATE"}}, tmp_path)


@pytest.mark.parametrize(
    "resolver, env, expected",
    [(tester_gate.resolve_cgroup_parent, {}, "cgroup_parent"),
     (tester_gate.resolve_memory, {}, "memory limit"),
     (tester_gate.resolve_memory_swap, {}, "memory-swap limit"),
     (tester_gate.resolve_cpus, {}, "CPU limit"),
     (tester_gate.resolve_cgroup_probe_image, {}, "cgroup probe image"),
     (tester_gate.resolve_dind_image, {}, "nested Docker image")],
)
def test_tester_gate_refuses_unconfigured_resource(resolver, env, expected, monkeypatch):
    if resolver is tester_gate.resolve_cgroup_parent:
        pytest.skip("cgroup_parent is declared-only since the CIU-46 wave: "
                    "unset resolves to None (announced unscoped launch), not a refusal")
    for name in ("CMRU_TESTER_CGROUP_PARENT", "CGROUP_PARENT_DEV_BACKGROUND",
                 "CMRU_TESTER_MEMORY", "CMRU_TESTER_MEMORY_SWAP", "CMRU_TESTER_CPUS",
                 "CMRU_TESTER_CGROUP_PROBE_IMAGE", "CMRU_TESTER_DIND_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match=expected):
        resolver(None)


def test_tester_gate_slice_probe_accepts_loaded_configured_host_unit(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        tester_gate.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="LoadState=loaded\nFragmentPath=/etc/systemd/x.slice\n", stderr=""
        ),
    )
    exists, note = tester_gate.check_slice_unit("build.slice", "debian:test")
    assert exists is True and "FragmentPath" in note


def test_tester_gate_slice_probe_rejects_transient_unit(monkeypatch):
    monkeypatch.setattr(tester_gate.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        tester_gate.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="LoadState=loaded\nFragmentPath=\n", stderr=""),
    )
    exists, note = tester_gate.check_slice_unit("typo.slice", "debian:test")
    assert exists is False and "TRANSIENT" in note


def test_tester_gate_docker_command_contains_mount_limits_and_command(tmp_path, monkeypatch):
    monkeypatch.setattr(tester_gate, "_physical_path", lambda path: path)
    monkeypatch.setattr(tester_gate, "_git_common_dir", lambda _path: None)
    argv = tester_gate.build_docker_command(
        tmp_path, "cmru", ["pytest", "-q"], image="tester:test",
        cgroup_parent="build.slice", memory="1g", memory_swap="2g", cpus="1.5",
        cgroup_parent_dev_background="background.slice",
    )
    assert "--memory" in argv and "1g" in argv
    assert "--cgroup-parent=build.slice" in argv
    assert "CGROUP_PARENT_DEV_BACKGROUND=background.slice" in argv
    assert argv[-3:] == ["tester:test", "pytest", "-q"]


def test_tester_gate_main_refuses_missing_image_before_host_launch(monkeypatch):
    for name in ("CMRU_TESTER_UNIFIED_IMAGE", "CMRU_TESTER_CGROUP_PARENT",
                 "CGROUP_PARENT_DEV_BACKGROUND"):
        monkeypatch.delenv(name, raising=False)
    # KI-17: the missing image now surfaces at the aggregate preflight, up
    # front and before any host/container launch, naming the variable.
    with pytest.raises(SystemExit, match="missing required configuration: CMRU_TESTER_UNIFIED_IMAGE"):
        tester_gate.main(["--cwd", ".", "--", "true"])


def test_standards_messages_distinguish_manual_projects_and_gate_contract():
    project = SimpleNamespace(template_revision=4, changelog="CHANGES.md", steps={}, runner_steps={})
    results = assess_projects(Path("."), {"demo": project}, [], ["demo"])
    assert results[0].problems == ()
    assert any("not in orchestration.project_order" in item for item in results[0].messages)
    assert any("one project-local release" in item for item in results[0].messages)
