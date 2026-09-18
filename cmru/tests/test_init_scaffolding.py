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
    # The generated orchestration contract deliberately requires the host's
    # gates tier; tests provide that declared fact instead of weakening the
    # shipped fail-closed configuration with a fallback.
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    (git_repo / "alpha").mkdir()
    (git_repo / "beta").mkdir()
    _feed_input(
        monkeypatch,
        [
            "acme", "vbpub", "org", "2", "alpha,beta",
            "alpha", "Alpha", "python", "wheel", "yes",
            "beta", "Beta", "generic", "bundle", "no", "make bundle", "make publish",
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
    _feed_input(monkeypatch, ["acme", "repo", "user", "1", "", "repo", "Repo", "python", "tarball", "yes", "make", "make publish"])
    plan = scaffold.collect_plan([], git_repo)
    assert plan["root"] == git_repo
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)
    assert [path.name for path, _ in files] == ["cmru.toml"]
    content = files[0][1]
    assert "[github]" in content and 'artifacts = ["tarball"]' in content


def test_wizard_refuses_existing_targets_before_writing(monkeypatch, git_repo, capsys):
    project = git_repo / "alpha"
    project.mkdir()
    existing = project / "cmru.toml"
    existing.write_text("# operator content\n", encoding="utf-8")
    _feed_input(monkeypatch, ["acme", "repo", "user", "2", "alpha", "alpha", "Alpha", "python", "wheel", "yes"])
    plan = scaffold.collect_plan([], git_repo)
    with pytest.raises(SystemExit):
        scaffold.build_files(plan, git_repo)
    assert "refusing to overwrite" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "# operator content\n"


def test_wizard_refuses_project_path_escape(monkeypatch, git_repo, tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    _feed_input(monkeypatch, ["acme", "repo", "user", "1", str(outside)])
    with pytest.raises(SystemExit):
        scaffold.collect_plan([], git_repo)
    assert "escapes CMRU root" in capsys.readouterr().err


def test_wizard_refuses_invalid_artifact_and_missing_generic_commands(monkeypatch, git_repo, capsys):
    _feed_input(monkeypatch, ["acme", "repo", "user", "1", "", "repo", "Repo", "python", "unknown"])
    with pytest.raises(SystemExit):
        scaffold.collect_plan([], git_repo)
    assert "artifact types" in capsys.readouterr().err

    _feed_input(monkeypatch, ["acme", "repo", "user", "1", "", "repo", "Repo", "python", "oci-image", "yes", "", ""])
    with pytest.raises(SystemExit):
        scaffold.collect_plan([], git_repo)
    assert "Build command" in capsys.readouterr().err


def test_old_init_project_option_is_rejected(git_repo, capsys):
    with pytest.raises(SystemExit):
        scaffold.collect_plan(["--project", "old"], git_repo)
    diagnostic = capsys.readouterr().err
    from cmru.cli_support import cmru_headline
    assert diagnostic.splitlines()[0] == cmru_headline()
    assert "--project was removed" in diagnostic


def test_wizard_accepts_all_artifact_types_and_requires_generic_commands(monkeypatch, git_repo):
    (git_repo / "alpha").mkdir()
    _feed_input(
        monkeypatch,
        ["acme", "repo", "org", "2", "alpha", "alpha", "Alpha", "generic", "all", "yes", "build all", "publish all"],
    )
    plan = scaffold.collect_plan([], git_repo)
    assert plan["projects"][0]["artifacts"] == ["wheel", "tarball", "bundle", "oci-image"]
    content = scaffold.build_files(plan, git_repo)[0][1]
    assert 'artifacts = ["wheel", "tarball", "bundle", "oci-image"]' in content
    assert 'argv = ["build", "all"]' in content
    assert 'argv = ["publish", "all"]' in content


def test_cli_init_help_has_version_headline(capsys):
    from cmru.cli import main, _cmru_version

    assert main(["init", "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"CMRU {_cmru_version()} — Configurable Multi Release Utility\n")
    assert "Guided scaffolding" in out


def test_cli_init_runs_interactive_adoption(monkeypatch, git_repo):
    from cmru.cli import main

    monkeypatch.chdir(git_repo)
    _feed_input(monkeypatch, ["repo", "user", "", "repo", "Repo", "python", "wheel", "yes", "yes"])
    assert main(["init", "--root", str(git_repo), "--owner", "acme", "--layout", "single"]) == 0
    assert (git_repo / "cmru.toml").is_file()


def test_cli_init_preview_can_be_refused_before_writing(monkeypatch, git_repo, capsys):
    monkeypatch.chdir(git_repo)
    _feed_input(monkeypatch, ["repo", "user", "", "repo", "Repo", "python", "wheel", "yes", "no"])

    with pytest.raises(SystemExit):
        from cmru.cli import main
        main(["init", "--root", str(git_repo), "--owner", "acme", "--layout", "single"])

    captured = capsys.readouterr()
    assert "CMRU init preview:" in captured.out
    assert "adoption cancelled" in captured.err
    assert not (git_repo / "cmru.toml").exists()
