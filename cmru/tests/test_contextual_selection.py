"""Focused behavioral tests for contextual CMRU discovery and selectors."""
from __future__ import annotations

from pathlib import Path

import pytest

from cmru.cli_support import TargetSelectionError, parse_target_names, select_target_names
from cmru.config import load_forge_config, resolve_invocation_context


def _project(name: str) -> str:
    return f'''schema_version = 1

[project]
id = "{name}"
description = "{name} project"
prefix = "{name}-v"
artifacts = ["wheel"]

[runtime]
kind = "none"

[project.version]
strategy = "scm"
bump = "patch"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

[steps.run-tests]
quiet = true
commands = [{{label = "test", argv = ["true"], cwd = "."}}]

[steps.build]
quiet = true
commands = [{{label = "build", argv = ["true"], cwd = "."}}]

[steps.push]
quiet = true
commands = [{{label = "push", argv = ["true"], cwd = "."}}]
'''


def _root_config(names: list[str]) -> str:
    order = ", ".join(repr(name) for name in names)
    entries = "\n".join(
        f"[orchestration.project.{name}]\nconfig = \"{name}/cmru.toml\"\ndepends_on = []\n"
        for name in names
    )
    return f'''schema_version = 1

[github]
owner = "acme"
repo = "estate"
owner_type = "org"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order = [{order}]
default_projects = [{order}]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"

{entries}
[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = []
ghcr_packages = ["*"]
ghcr_delete_packages = []
'''


def test_selector_trims_and_returns_declared_order():
    projects = {name: object() for name in ("ciu", "assay", "nyxloom")}
    assert parse_target_names(" assay, ciu ") == ["assay", "ciu"]
    assert select_target_names(
        "assay, ciu", projects, ["ciu", "assay", "nyxloom"]
    ) == ["ciu", "assay"]
    with pytest.raises(TargetSelectionError, match="exclusive"):
        parse_target_names("all,ciu")
    with pytest.raises(TargetSelectionError, match="duplicate"):
        parse_target_names("ciu,ciu")
    with pytest.raises(TargetSelectionError, match="empty"):
        parse_target_names("ciu,")


def test_nearest_root_can_serve_multiple_repositories_and_project_context(tmp_path):
    (tmp_path / "repo-a").mkdir()
    (tmp_path / "repo-b" / "nested").mkdir(parents=True)
    for name, root in (("repo-a", tmp_path / "repo-a"), ("repo-b", tmp_path / "repo-b")):
        (root / "cmru.toml").write_text(_project(name.replace("repo-", "")), encoding="utf-8")
    (tmp_path / "cmru.orchestration.toml").write_text(
        _root_config(["a", "b"]).replace('config = "a/cmru.toml"', 'config = "repo-a/cmru.toml"')
        .replace('config = "b/cmru.toml"', 'config = "repo-b/cmru.toml"'),
        encoding="utf-8",
    )
    forge = load_forge_config(tmp_path / "cmru.orchestration.toml")
    assert forge.repo_root == tmp_path.resolve()
    assert resolve_invocation_context(cwd=tmp_path / "repo-b" / "nested").project_name == "b"
    assert resolve_invocation_context(cwd=tmp_path).scope == "estate"


def test_nearest_nested_root_wins_and_explicit_config_wins(tmp_path):
    (tmp_path / "outer").mkdir()
    (tmp_path / "outer" / "inner").mkdir()
    (tmp_path / "outer" / "cmru.orchestration.toml").write_text(_root_config(["outer"]), encoding="utf-8")
    (tmp_path / "outer" / "outer").mkdir()
    (tmp_path / "outer" / "outer" / "cmru.toml").write_text(_project("outer"), encoding="utf-8")
    (tmp_path / "outer" / "inner" / "cmru.orchestration.toml").write_text(_root_config(["inner"]), encoding="utf-8")
    (tmp_path / "outer" / "inner" / "inner").mkdir()
    (tmp_path / "outer" / "inner" / "inner" / "cmru.toml").write_text(_project("inner"), encoding="utf-8")
    nested = resolve_invocation_context(cwd=tmp_path / "outer" / "inner" / "inner")
    assert nested.cmru_root == (tmp_path / "outer" / "inner").resolve()
    explicit = resolve_invocation_context(tmp_path / "outer" / "cmru.orchestration.toml", cwd=tmp_path / "outer" / "inner")
    assert explicit.cmru_root == (tmp_path / "outer").resolve()


def test_symlink_project_escape_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cmru.toml").write_text(_project("escape"), encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "cmru.orchestration.toml").write_text(
        _root_config(["escape"]).replace('config = "escape/cmru.toml"', 'config = "link/cmru.toml"'),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        load_forge_config(root / "cmru.orchestration.toml")


def test_nested_unregistered_project_is_not_routed_to_outer_project(tmp_path, capsys):
    root = tmp_path / "root"
    outer = root / "outer"
    nested = outer / "nested" / "src"
    nested.mkdir(parents=True)
    (outer / "cmru.toml").write_text(_project("outer"), encoding="utf-8")
    (outer / "nested" / "cmru.toml").write_text(_project("nested"), encoding="utf-8")
    (root / "cmru.orchestration.toml").write_text(
        _root_config(["outer"]).replace(
            'config = "outer/cmru.toml"', 'config = "outer/cmru.toml"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        resolve_invocation_context(cwd=nested)
    assert "not registered" in capsys.readouterr().err
