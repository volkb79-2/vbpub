"""Exact validation and changelog branch witnesses."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import changelog, config, exit_codes


def test_config_version_rejects_invalid_strategy_and_accepts_external():
    with pytest.raises(SystemExit) as error:
        config._parse_version({"strategy": "guess", "bump": "patch"}, "demo")
    assert error.value.code == exit_codes.CONFIG_ERROR
    parsed = config._parse_version({"strategy": "external:VERSION", "bump": "conventional", "paths": ["src"]}, "demo")
    assert parsed.strategy == "external:VERSION" and parsed.paths == ["src"]


def test_config_variants_reject_duplicate_and_unsafe_names():
    with pytest.raises(SystemExit) as error:
        config._parse_variants("demo", {"variants": [{"name": "../escape"}]})
    assert error.value.code == exit_codes.CONFIG_ERROR
    with pytest.raises(SystemExit) as error:
        config._parse_variants("demo", {"variants": [{"name": "py311"}, {"name": "py311"}]})
    assert error.value.code == exit_codes.CONFIG_ERROR


def test_config_orchestration_dependency_order_rejects_late_provider(tmp_path):
    # Exercise the strict dependency-order policy through the parsed document.
    path = tmp_path / "cmru.orchestration.toml"
    path.write_text(
        "schema_version=1\n[orchestration]\nproject_order=['consumer','provider']\n"
        "default_projects=['consumer']\ndefault_steps=[]\nexecution_mode='project-first'\n"
        "[orchestration.project.consumer]\nconfig='consumer/cmru.toml'\ndepends_on=['provider']\n"
        "[orchestration.project.provider]\nconfig='provider/cmru.toml'\n"
        "[cleanup]\nrelease_tag_prefixes=[]\nkeep_release_tags=[]\nghcr_packages=[]\nghcr_delete_packages=[]\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer").mkdir(); (tmp_path / "provider").mkdir()
    for name in ("consumer", "provider"):
        (tmp_path / name / "cmru.toml").write_text(
            "schema_version=1\n[github]\nowner='o'\nrepo='r'\nowner_type='org'\n"
            "[targets]\nhost='github'\nregistry=[]\n[project]\nid='" + name + "'\n"
            "description='demo'\nprefix='" + name + "-v'\nartifacts=['bundle']\n"
            "[project.version]\nstrategy='scm'\nbump='patch'\n[project.release]\n"
            "git_tag=false\nbuild_step='build'\nartifact_dirs=['dist']\n",
            encoding="utf-8",
        )
    with pytest.raises(SystemExit) as error:
        config.load_forge_config(path, require_orchestration=True)
    assert error.value.code == exit_codes.CONFIG_ERROR


def test_config_project_and_orchestration_tables_fail_closed(tmp_path, capsys):
    project = tmp_path / "cmru.toml"
    project.write_text(
        "schema_version=1\n[github]\nowner='o'\nrepo='r'\nowner_type='org'\n"
        "[targets]\nhost='github'\nregistry=[]\n[project]\nid='demo'\n"
        "description='demo'\nprefix='demo-v'\nartifacts=['wheel']\n"
        "[project.version]\nstrategy='scm'\nbump='patch'\n"
        "[project.release]\ngit_tag=false\nbuild_step='build'\nartifact_dirs=[]\n",
        encoding="utf-8",
    )
    cases = [
        ("build_metadata={bad='x'}\n", "build_metadata only permits", "before_github"),
        ("project_metadata='wrong'\n", "project_metadata] must be a table", "before_github"),
        ("release='wrong'\n", "project.release is required", "inside_project"),
    ]
    for prefix, diagnostic, placement in cases:
        raw = project.read_text(encoding="utf-8")
        if placement == "before_github":
            mutated = raw.replace("[github]\n", prefix + "[github]\n")
        else:
            mutated = raw.split("[project.release]", 1)[0].replace("[project.version]\n", prefix + "[project.version]\n")
        project.write_text(mutated, encoding="utf-8")
        with pytest.raises(SystemExit) as error:
            config._parse_project_document(project)
        assert error.value.code == exit_codes.CONFIG_ERROR
        assert diagnostic in capsys.readouterr().out
        project.write_text(raw, encoding="utf-8")


def test_config_orchestration_requires_declared_projects_and_dependencies_in_order(monkeypatch, tmp_path, capsys):
    github = config.GitHubS2Config("o", "r", "org", None)
    targets = config.TargetsConfig("github", [])

    def fake_project(path):
        name = path.parent.name
        return config.ProjectS2Config(name, None, f"{name}-v", [], None, None, None, {}, project_root=path.parent), github, targets

    monkeypatch.setattr(config, "_parse_project_document", fake_project)
    monkeypatch.setattr(config, "_load_repository_secrets", lambda *args: (None, {}))
    path = tmp_path / "cmru.orchestration.toml"

    def run(order, entries):
        path.write_text(
            "schema_version=1\n[orchestration]\nproject_order=" + repr(order) + "\n"
            "default_projects=" + repr(order[:1]) + "\ndefault_steps=[]\nexecution_mode='project-first'\n" + entries,
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as error:
            config._load_orchestration_config(path)
        assert error.value.code == exit_codes.CONFIG_ERROR
        return capsys.readouterr().out

    assert "must include declared project" in run(
        ["provider"], "[orchestration.project.provider]\nconfig='provider/cmru.toml'\n"
        "[orchestration.project.consumer]\nconfig='consumer/cmru.toml'\n",
    )
    assert "must include dependency" in run(
        ["consumer"], "[orchestration.project.consumer]\nconfig='consumer/cmru.toml'\ndepends_on=['provider']\n"
        "[orchestration.project.provider]\nconfig='provider/cmru.toml'\n",
    )
    assert "appear earlier" in run(
        ["consumer", "provider"], "[orchestration.project.consumer]\nconfig='consumer/cmru.toml'\ndepends_on=['provider']\n"
        "[orchestration.project.provider]\nconfig='provider/cmru.toml'\n",
    )


def test_config_orchestration_missing_tables_are_rejected(tmp_path, capsys):
    path = tmp_path / "cmru.orchestration.toml"
    cases = [
        ("schema_version=1\n", "[orchestration] is required"),
        ("schema_version=1\n[orchestration]\nproject_order=[]\ndefault_projects=[]\n"
         "default_steps=[]\nexecution_mode='project-first'\n", "entries are required"),
        ("schema_version=1\n[orchestration]\nproject_order=['demo']\ndefault_projects=['demo']\n"
         "default_steps=[]\nexecution_mode='project-first'\n[orchestration.project]\ndemo='bad'\n", "must be a table"),
    ]
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit) as error:
            config.load_forge_config(path, require_orchestration=True)
        assert error.value.code == exit_codes.CONFIG_ERROR
        assert diagnostic in capsys.readouterr().out


def test_changelog_plan_rejects_no_changes_and_empty_external_variable(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", prefix="demo-v", version=SimpleNamespace(strategy="external:"))
    monkeypatch.setattr(changelog, "detect_changed_projects", lambda *a: [])
    with pytest.raises(RuntimeError, match="no changes"):
        changelog._project_release_plan(tmp_path, project)
    monkeypatch.setattr(changelog, "detect_changed_projects", lambda *a: [("demo", project, "demo-v1.0.0", "patch")])
    with pytest.raises(RuntimeError, match="no variable"):
        changelog._project_release_plan(tmp_path, project)


def test_changelog_plan_selects_external_explicit_major_counter_and_initial_versions(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", prefix="demo-v", git_tag=True)
    monkeypatch.setattr(changelog, "detect_changed_projects", lambda *a: [("demo", project, "demo-v1.2.3", "patch")])
    monkeypatch.setattr(changelog, "_external_version", lambda *_: "4.5.6")
    project.version = SimpleNamespace(strategy="external:VERSION")
    assert changelog._project_release_plan(tmp_path, project) == ("4.5.6", "demo-v1.2.3")
    project.version = SimpleNamespace(strategy="scm")
    assert changelog._project_release_plan(tmp_path, project, set_version="9.0.0")[0] == "9.0.0"
    assert changelog._project_release_plan(tmp_path, project, major=True)[0] == "2.0.0"
    monkeypatch.setattr(changelog, "_next_counter_version", lambda *_: "1.0.7")
    project.version = SimpleNamespace(strategy="counter", base_version="1.0.0")
    assert changelog._project_release_plan(tmp_path, project)[0] == "1.0.7"
    project.version = SimpleNamespace(strategy="scm")
    monkeypatch.setattr(changelog, "detect_changed_projects", lambda *a: [("demo", project, None, "patch")])
    assert changelog._project_release_plan(tmp_path, project, minor=True)[0] == "0.1.0"
    assert changelog._project_release_plan(tmp_path, project)[0] == "0.1.0"


def test_changelog_generated_output_change_reports_git_status_failure(monkeypatch, tmp_path):
    project = SimpleNamespace(name="demo", cwd="demo", commit_generated=("VERSION",))
    monkeypatch.setattr(changelog.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="status failed"))
    with pytest.raises(RuntimeError, match="git status"):
        changelog._generated_outputs_changed(tmp_path, project)


def test_changelog_previous_project_tag_returns_stripped_success(monkeypatch, tmp_path):
    monkeypatch.setattr(changelog.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=" demo-v1.2.3 \n", stderr=""))
    assert changelog._previous_project_tag(tmp_path, "demo-v", "demo-v1.3.0") == "demo-v1.2.3"


def test_changelog_backfill_inserts_marked_history_after_existing_marker(tmp_path, monkeypatch):
    root = tmp_path / "demo"; root.mkdir()
    path = root / "CHANGES.md"; path.write_text("# Changes\n\n<!-- cmru: release history -->\n", encoding="utf-8")
    project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md", prefix="demo-v")
    monkeypatch.setattr(changelog, "_git", lambda *args, **kwargs: "a" * 40 if "show" not in args else "2026-01-01")
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *args: None)
    monkeypatch.setattr(changelog, "_subject_groups", lambda *args, **kwargs: {})
    assert changelog.backfill_release_changelog(tmp_path, project, "demo-v1.0.0") is True
    assert "backfilled-after-release" in path.read_text()
