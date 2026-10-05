"""CMRU project-framework marker checks and safe update behaviour."""
from __future__ import annotations

from pathlib import Path

import pytest

from cmru.standards import standards_main


ORCHESTRATION = """schema_version = 1
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[orchestration]
project_order = ["demo"]
default_projects = ["demo"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
[orchestration.project.demo]
config = "demo/cmru.toml"
depends_on = []
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = ["*"]
ghcr_delete_packages = []
"""

PROJECT = """schema_version = 1
[env]
CMRU_TESTER_UNIFIED_IMAGE = "tester-unified:test"
CMRU_TESTER_MEMORY = "3g"
CMRU_TESTER_MEMORY_SWAP = "16g"
CMRU_TESTER_CPUS = "1.5"
CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian:test"
[runtime]
kind = "none"
[project]
id = "demo"
description = "demo"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "conventional"
[project.release]
git_tag = true
build_step = "build"
[steps.run-tests]
quiet = true
commands = [{ label = "gate", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
"""


def _config(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text(ORCHESTRATION, encoding="utf-8")
    project = tmp_path / "demo" / "cmru.toml"
    project.parent.mkdir()
    project.write_text(PROJECT, encoding="utf-8")
    return path, project


def test_standards_reports_missing_project_marker(tmp_path):
    config, _project = _config(tmp_path)
    assert standards_main(["demo", "--config", str(config)]) == 2


def test_standards_update_only_touches_project_marker_and_rechecks(tmp_path):
    config, project = _config(tmp_path)

    assert standards_main(["demo", "--config", str(config), "--update"]) == 0

    updated = project.read_text(encoding="utf-8")
    assert "[project]\ntemplate_revision = 4\n" in updated
    assert "commands = [{ label = \"gate\", argv = [\"true\"], cwd = \".\" }]" in updated


def test_standards_rejects_noisy_default_step_output(tmp_path):
    config, project = _config(tmp_path)
    project.write_text(
        project.read_text(encoding="utf-8").replace("quiet = true", "quiet = false", 1),
        encoding="utf-8",
    )

    assert standards_main(["demo", "--config", str(config)]) == 2


def test_standards_requires_explicit_tester_gate_inputs(tmp_path):
    config, project = _config(tmp_path)
    contents = project.read_text(encoding="utf-8")
    contents = contents.replace(
        'CMRU_TESTER_CPUS = "1.5"\n', "",
    ).replace(
        'argv = ["true"]', 'argv = ["cmru", "tester-gate", "--cwd", ".", "--", "true"]',
        1,
    )
    project.write_text(contents, encoding="utf-8")

    assert standards_main(["demo", "--config", str(config)]) == 2


def test_standards_requires_dind_image_only_for_a_docker_enabled_gate(tmp_path):
    config, project = _config(tmp_path)
    contents = project.read_text(encoding="utf-8").replace(
        'argv = ["true"]',
        'argv = ["cmru", "tester-gate", "--cwd", ".", "--enable-docker", "--", "true"]',
        1,
    )
    project.write_text(contents, encoding="utf-8")

    assert standards_main(["demo", "--config", str(config)]) == 2


def test_standards_requires_a_wheel_builder_image_for_wheel_build(tmp_path):
    config, project = _config(tmp_path)
    contents = project.read_text(encoding="utf-8").replace(
        'argv = ["true"]',
        'argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."]',
        1,
    )
    project.write_text(contents, encoding="utf-8")

    assert standards_main(["demo", "--config", str(config)]) == 2


def _gate_project(tmp_path: Path, *, docker: bool, extra_env: str = "") -> tuple[Path, Path]:
    """A project whose run-tests step is a tester-gate, with the template
    marker already applied so the verdict reflects only the tester contract."""
    config, project = _config(tmp_path)
    flag = '"--enable-docker", ' if docker else ""
    contents = project.read_text(encoding="utf-8").replace(
        'argv = ["true"]',
        f'argv = ["cmru", "tester-gate", "--cwd", ".", {flag}"--", "true"]',
        1,
    ).replace(
        'CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian:test"\n',
        'CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian:test"\n'
        'CMRU_TESTER_CGROUP_PARENT = "dev-gates.slice"\n' + extra_env,
    )
    project.write_text(contents, encoding="utf-8")
    standards_main(["demo", "--config", str(config), "--update"])
    return config, project


def test_standards_requires_the_pids_limit_for_a_tester_gate(tmp_path, capsys):
    """BG-01(a'): CMRU_TESTER_PIDS_LIMIT is part of the shared required set,
    so `cmru standards` rejects a tester-gate project that omits it."""
    config, _project = _gate_project(tmp_path, docker=False)
    capsys.readouterr()
    assert standards_main(["demo", "--config", str(config)]) == 2
    assert "requires explicit [env] values: CMRU_TESTER_PIDS_LIMIT" in capsys.readouterr().out

    (tmp_path / "ok").mkdir()
    config, _project = _gate_project(
        tmp_path / "ok", docker=False, extra_env='CMRU_TESTER_PIDS_LIMIT = "4096"\n',
    )
    capsys.readouterr()
    assert standards_main(["demo", "--config", str(config)]) == 0


def test_standards_requires_every_dind_limit_for_a_docker_enabled_gate(tmp_path, capsys):
    """BG-07: the sidecar's memory/CPU/pids limits are required with
    --enable-docker, each named in the report."""
    config, _project = _gate_project(
        tmp_path, docker=True,
        extra_env='CMRU_TESTER_PIDS_LIMIT = "4096"\nCMRU_TESTER_DIND_IMAGE = "docker@sha256:x"\n',
    )
    capsys.readouterr()
    assert standards_main(["demo", "--config", str(config)]) == 2
    out = capsys.readouterr().out
    assert ("Docker-enabled tester-gate requires explicit CMRU_TESTER_DIND_MEMORY, "
            "CMRU_TESTER_DIND_CPUS, CMRU_TESTER_DIND_PIDS_LIMIT in [env]") in out
