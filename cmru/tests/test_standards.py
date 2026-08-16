"""CMRU project-framework marker checks and safe update behaviour."""
from __future__ import annotations

from pathlib import Path

import pytest

from cmru.standards import standards_main


ORCHESTRATION = """schema_version = 1
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
[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
[targets]
host = "github"
registry = []
[env]
CMRU_TESTER_UNIFIED_IMAGE = "tester-unified:test"
CMRU_TESTER_MEMORY = "3g"
CMRU_TESTER_MEMORY_SWAP = "16g"
CMRU_TESTER_CPUS = "1.5"
CMRU_TESTER_CGROUP_PROBE_IMAGE = "debian:test"
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
    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])
    assert exc.value.code == 2


def test_standards_update_only_touches_project_marker_and_rechecks(tmp_path):
    config, project = _config(tmp_path)

    standards_main(["--config", str(config), "--project", "demo", "--update"])

    updated = project.read_text(encoding="utf-8")
    assert "[project]\ntemplate_revision = 4\n" in updated
    assert "commands = [{ label = \"gate\", argv = [\"true\"], cwd = \".\" }]" in updated


def test_standards_rejects_noisy_default_step_output(tmp_path):
    config, project = _config(tmp_path)
    project.write_text(
        project.read_text(encoding="utf-8").replace("quiet = true", "quiet = false", 1),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])

    assert exc.value.code == 2


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

    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])

    assert exc.value.code == 2


def test_standards_requires_dind_image_only_for_a_docker_enabled_gate(tmp_path):
    config, project = _config(tmp_path)
    contents = project.read_text(encoding="utf-8").replace(
        'argv = ["true"]',
        'argv = ["cmru", "tester-gate", "--cwd", ".", "--enable-docker", "--", "true"]',
        1,
    )
    project.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])

    assert exc.value.code == 2


def test_standards_requires_a_wheel_builder_image_for_wheel_build(tmp_path):
    config, project = _config(tmp_path)
    contents = project.read_text(encoding="utf-8").replace(
        'argv = ["true"]',
        'argv = ["python3", "-m", "cmru.handlers", "wheel-build", "--cwd", "."]',
        1,
    )
    project.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])

    assert exc.value.code == 2
