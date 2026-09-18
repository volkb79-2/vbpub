"""Behavioral tests for the interactive CMRU adoption wizard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru import scaffold  # noqa: E402
from cmru.config import load_forge_config  # noqa: E402


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-qb", "main", "."], cwd=root, check=True, capture_output=True)
    return root


def _feed_input(monkeypatch, answers):
    values = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(values))


def test_monorepo_wizard_renders_central_facts_and_custom_commands(monkeypatch, git_repo):
    (git_repo / "alpha").mkdir()
    (git_repo / "beta").mkdir()
    _feed_input(
        monkeypatch,
        [
            "acme", "2", "alpha,beta",
            "alpha", "Alpha", "wheel", "yes",
            "beta", "Beta", "bundle", "no", "make bundle", "make publish",
        ],
    )
    plan = scaffold.collect_plan([], git_repo)
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)

    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cfg = load_forge_config(git_repo / "cmru.orchestration.toml", require_orchestration=True)
    assert list(cfg.projects) == ["alpha", "beta"]
    assert "[github]" not in (git_repo / "alpha/cmru.toml").read_text()
    beta = (git_repo / "beta/cmru.toml").read_text()
    assert 'argv = ["make", "bundle"]' in beta
    assert 'argv = ["make", "publish"]' in beta


def test_single_project_wizard_allows_same_folder_and_keeps_standalone_facts(monkeypatch, git_repo):
    _feed_input(monkeypatch, ["acme", "1", "", "repo", "Repo", "tarball", "yes", "make", "make publish"])
    plan = scaffold.collect_plan([], git_repo)
    assert plan["root"] == git_repo
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)
    assert [path.name for path, _ in files] == ["cmru.toml"]
    content = files[0][1]
    assert "[github]" in content and 'artifacts = ["tarball"]' in content


def test_wizard_refuses_existing_targets_before_writing(monkeypatch, git_repo):
    project = git_repo / "alpha"
    project.mkdir()
    existing = project / "cmru.toml"
    existing.write_text("# operator content\n", encoding="utf-8")
    _feed_input(monkeypatch, ["acme", "2", "alpha", "alpha", "Alpha", "wheel", "yes"])
    plan = scaffold.collect_plan([], git_repo)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, git_repo)
    assert existing.read_text(encoding="utf-8") == "# operator content\n"


def test_wizard_refuses_project_path_escape(monkeypatch, git_repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _feed_input(monkeypatch, ["acme", "1", str(outside)])
    with pytest.raises(SystemExit, match="escapes CMRU root"):
        scaffold.collect_plan([], git_repo)


def test_wizard_refuses_invalid_artifact_and_missing_generic_commands(monkeypatch, git_repo):
    _feed_input(monkeypatch, ["acme", "1", "", "repo", "Repo", "unknown"])
    with pytest.raises(SystemExit, match="artifact template"):
        scaffold.collect_plan([], git_repo)

    _feed_input(monkeypatch, ["acme", "1", "", "repo", "Repo", "oci-image", "yes", "", ""])
    with pytest.raises(SystemExit, match="require explicit build and publish"):
        scaffold.collect_plan([], git_repo)


def test_old_init_project_option_is_rejected(git_repo):
    with pytest.raises(SystemExit, match="--project was removed"):
        scaffold.collect_plan(["--project", "old"], git_repo)


def test_cli_init_help_has_version_headline(capsys):
    from cmru.cli import main, _cmru_version

    assert main(["init", "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"CMRU {_cmru_version()} — Configurable Multi Release Utility\n")
    assert "Guided scaffolding" in out


def test_cli_init_runs_interactive_adoption(monkeypatch, git_repo):
    from cmru.cli import main

    monkeypatch.chdir(git_repo)
    _feed_input(monkeypatch, ["", "repo", "Repo", "wheel", "yes"])
    assert main(["init", "--root", str(git_repo), "--owner", "acme", "--layout", "single"]) == 0
    assert (git_repo / "cmru.toml").is_file()
