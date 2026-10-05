"""CLI-04: ``orchestration.default_projects`` is deprecated, ignored, and optional."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from cmru import cli, cli_support, getpy, resolve, standards, tool_deps
from cmru.config import load_forge_config

PROJECT = """schema_version = 1

[runtime]
kind = "none"

[project]
id = "{name}"
description = "d"
template_revision = {revision}
prefix = "{name}-v"
artifacts = ["wheel"]
scm_dist = "{name}"

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

[steps.run-tests]
quiet = true
commands = [{{ label = "t", argv = ["true"], cwd = "." }}]

[steps.build]
quiet = true
commands = [{{ label = "b", argv = ["true"], cwd = "." }}]

[steps.push]
quiet = true
commands = [{{ label = "p", argv = ["true"], cwd = "." }}]
"""

CENTRAL = """schema_version = 1

[github]
owner = "o"
repo = "r"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order = ["a", "b"]
{extra}default_steps = ["build"]
execution_mode = "project-first"

[orchestration.project.a]
config = "a/cmru.toml"
depends_on = []

[orchestration.project.b]
config = "b/cmru.toml"
depends_on = []

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
"""


def _estate(tmp_path: Path, extra: str) -> Path:
    from cmru.standards import PROJECT_TEMPLATE_REVISION

    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "cmru.toml").write_text(
            PROJECT.format(name=name, revision=PROJECT_TEMPLATE_REVISION), encoding="utf-8",
        )
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(CENTRAL.format(extra=extra), encoding="utf-8")
    return central


def test_default_projects_is_no_longer_required(tmp_path):
    forge = load_forge_config(_estate(tmp_path, ""), require_orchestration=True)

    assert forge.orchestration.project_order == ["a", "b"]


def test_default_projects_is_accepted_with_a_warning_and_ignored(tmp_path, capsys):
    central = _estate(tmp_path, 'default_projects = ["a"]\n')

    forge = load_forge_config(central, require_orchestration=True)

    err = capsys.readouterr().err
    assert "orchestration.default_projects is ignored and will be removed" in err
    assert forge.orchestration.project_order == ["a", "b"]
    # An omitted target at the estate root still selects everything: the key
    # never narrowed it (the old finding's probe selected a and b).
    assert cli_support.select_target_names(None, forge.projects, ["a", "b"]) == ["a", "b"]


def test_an_unknown_default_projects_entry_is_not_validated_any_more(tmp_path, capsys):
    central = _estate(tmp_path, 'default_projects = ["ghost"]\n')

    load_forge_config(central, require_orchestration=True)

    assert "ignored" in capsys.readouterr().err


def test_estate_scope_parameter_is_gone():
    assert "estate_scope" not in inspect.signature(cli_support.select_target_names).parameters


@pytest.mark.parametrize("module", [cli, resolve, getpy, standards, tool_deps])
def test_no_help_text_promises_an_estate_default(module):
    source = inspect.getsource(module)
    assert "estate default" not in source
    assert "every orchestrated project at the estate root" in source
