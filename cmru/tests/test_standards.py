"""CMRU project-framework marker checks and safe update behaviour."""
from __future__ import annotations

from pathlib import Path

import pytest

from cmru.standards import standards_main


CONFIG = """
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
default_steps = ["run-tests", "build"]
execution_mode = "project-first"

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = ["*"]
ghcr_delete_packages = []

[project.demo]
prefix = "demo-v"
artifacts = ["wheel"]
cwd = "demo"
[project.demo.version]
strategy = "scm"
[project.demo.steps.run-tests]
commands = [{ label = "gate", argv = ["true"], cwd = "." }]
"""


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "cmru.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_standards_reports_missing_project_marker(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        standards_main(["--config", str(config), "--project", "demo"])
    assert exc.value.code == 2


def test_standards_update_only_touches_markers_and_rechecks(tmp_path):
    config = _config(tmp_path)
    runner_config = tmp_path / "demo" / "cmru.build.toml"
    runner_config.parent.mkdir()
    runner_config.write_text("project_root = \".\"\n", encoding="utf-8")

    standards_main(["--config", str(config), "--project", "demo", "--update"])

    updated = config.read_text(encoding="utf-8")
    assert "[project.demo]\ntemplate_revision = 1\n" in updated
    assert runner_config.read_text(encoding="utf-8").startswith(
        "# cmru-runner-template: build-config\n# cmru-runner-template-revision: 1\n"
    )
    assert "project_root = \".\"" in runner_config.read_text(encoding="utf-8")
