"""``cmru.runner.prepare_project_environment``: the supported Python API that applies a
project's declared CMRU environment WITHOUT executing a step.

The unit under test is never mocked.  The happy paths run it against real
``cmru.toml`` / ``cmru.orchestration.toml`` documents.  Two defensive refusals (an
orchestration that registers no/several projects at the path, a project document that
declares a project count other than one) cannot be produced by a config that
``load_config`` accepts, so for those alone the *collaborator* ``cmru.cli.load_config``
is replaced; the function itself still runs.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import runner

_CENTRAL_FACTS = """[github]
owner = "octocat"
repo = "demo"
owner_type = "user"
token_source = "cmru"
[targets]
host = "github"
registry = ["ghcr.io"]
"""

_PROJECT_CORE = """[runtime]
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
[env]
PREPARE_ENV_FLAG = "on"
[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]
[steps.build]
quiet = true
commands = [{ label = "build", argv = ["bash", "-c", "touch step-ran"], cwd = "." }]
[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
"""

_PROJECT = "schema_version = 1\n" + _CENTRAL_FACTS + _PROJECT_CORE
_PROJECT_CENTRAL = "schema_version = 1\n" + _PROJECT_CORE

_ORCHESTRATION = """schema_version = 1
[github]
owner = "octocat"
repo = "central"
owner_type = "user"
token_source = "cmru"
[targets]
host = "github"
registry = ["registry.example"]
[orchestration]
project_order = ["demo"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
[orchestration.project.demo]
config = "demo/cmru.toml"
depends_on = []
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
"""

_TOUCHED = ("REGISTRY", "GITHUB_USERNAME", "GITHUB_REPO", "PREPARE_ENV_FLAG")


@pytest.fixture(autouse=True)
def private_environ(monkeypatch):
    """Everything the API writes lands in a private copy and is discarded."""
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in _TOUCHED:
        os.environ.pop(name, None)


def test_a_project_local_config_applies_its_environment_and_runs_no_step(tmp_path):
    project = tmp_path / "demo"
    project.mkdir()
    config = project / "cmru.toml"
    config.write_text(_PROJECT, encoding="utf-8")

    assert runner.prepare_project_environment(config) is None

    assert os.environ["REGISTRY"] == "ghcr.io"
    assert os.environ["GITHUB_USERNAME"] == "octocat"
    assert os.environ["GITHUB_REPO"] == "demo"
    assert os.environ["PREPARE_ENV_FLAG"] == "on"
    assert not (project / "step-ran").exists()  # the declared build step was not executed


def test_the_nearest_orchestration_config_supplies_the_environment(tmp_path):
    (tmp_path / "cmru.orchestration.toml").write_text(_ORCHESTRATION, encoding="utf-8")
    project = tmp_path / "demo"
    project.mkdir()
    config = project / "cmru.toml"
    config.write_text(_PROJECT_CENTRAL, encoding="utf-8")

    runner.prepare_project_environment(config)

    assert os.environ["REGISTRY"] == "registry.example"  # central policy wins
    assert os.environ["GITHUB_REPO"] == "central"
    assert os.environ["PREPARE_ENV_FLAG"] == "on"  # the project's own [env] is merged in


def test_an_orchestration_that_does_not_register_exactly_one_project_at_the_path_is_refused(
    monkeypatch, tmp_path,
):
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(_ORCHESTRATION, encoding="utf-8")
    elsewhere = SimpleNamespace(project_root=tmp_path / "elsewhere", env={})
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda _path: (tmp_path, {"x": elsewhere}, [], {}, {}, "", [], {}, SimpleNamespace(), SimpleNamespace()),
    )
    monkeypatch.setattr(
        "cmru.config.resolve_invocation_context",
        lambda **_: SimpleNamespace(config_path=central),
    )
    with pytest.raises(RuntimeError, match="does not register exactly one project"):
        runner.prepare_project_environment(tmp_path / "demo" / "cmru.toml")


def test_a_project_document_must_declare_exactly_one_project(monkeypatch, tmp_path):
    config = tmp_path / "cmru.toml"
    config.write_text("schema_version = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "cmru.config.resolve_invocation_context",
        lambda **_: SimpleNamespace(config_path=config),
    )
    monkeypatch.setattr(
        "cmru.cli.load_config",
        lambda _path: (tmp_path, {}, [], {}, {}, "", [], {}, SimpleNamespace(), SimpleNamespace()),
    )
    with pytest.raises(RuntimeError, match="declares exactly one project"):
        runner.prepare_project_environment(config)
