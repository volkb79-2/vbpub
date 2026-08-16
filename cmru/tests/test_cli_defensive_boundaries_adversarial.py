import runpy
import sys
from types import SimpleNamespace

import pytest

from cmru import cli


def test_load_config_refuses_project_root_outside_orchestration_root(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    project_config = outside / "cmru.toml"
    project_config.write_text(
        "[project]\nartifacts = []\n[project.release]\ngit_tag = true\n[steps]\n",
        encoding="utf-8",
    )
    parsed = SimpleNamespace(
        template_revision=None, env={}, prefix="demo-v", scm_dist=None,
        changelog="CHANGES.md", build_metadata={}, artifact_dirs=(), build_step="",
    )
    orchestration = SimpleNamespace(
        project_configs={"demo": project_config}, project_order=["demo"],
        default_projects=["demo"], default_steps=[], execution_mode="project-first",
        dependencies={},
    )
    forge = SimpleNamespace(
        repo_root=repo_root, projects={"demo": parsed}, orchestration=orchestration,
        project_tokens={}, github=SimpleNamespace(owner="o", repo="r", token="", owner_type="user"),
        targets=SimpleNamespace(registry=[]), cleanup=None, env={},
    )
    monkeypatch.setattr(cli, "load_forge_config", lambda _: forge)
    with pytest.raises(ValueError, match="project root is outside orchestration root"):
        cli.load_config(tmp_path / "cmru.toml", validate_dependencies=False)


def test_source_tree_version_returns_none_for_non_checkout_path(monkeypatch, tmp_path):
    module = tmp_path / "installed" / "cmru" / "cli.py"
    module.parent.mkdir(parents=True)
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(module))
    assert cli._source_tree_version() is None


def test_cli_module_guard_runs_main_for_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cmru", "--help"])
    runpy.run_path(str(cli.__file__), run_name="__main__")
    assert "Configurable Multi Release Utility" in capsys.readouterr().out
