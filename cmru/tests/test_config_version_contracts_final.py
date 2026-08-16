"""Source-guided contracts for strict config and version orchestration."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import config, version


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"; root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "test")
    (root / "demo").mkdir(); (root / "demo" / "source.py").write_text("x=1\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "initial")
    return root


def project_toml() -> str:
    return '''schema_version = 1
[github]
owner = "acme"
repo = "vbpub"
owner_type = "org"
[targets]
host = "github"
registry = []
[project]
id = "demo"
description = "demo project"
prefix = "demo-v"
artifacts = ["wheel"]
[project.version]
strategy = "scm"
bump = "patch"
[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]
[steps.run-tests]
quiet = true
commands = [{label = "tests", argv = ["echo", "ok"], cwd = "."}]
[steps.build]
quiet = true
commands = [{label = "build", argv = ["echo", "ok"], cwd = "."}]
[steps.push]
quiet = true
commands = [{label = "push", argv = ["echo", "ok"], cwd = "."}]
'''


def orch_toml(entry: str = 'config = "demo/cmru.toml"', order: str = '["demo"]') -> str:
    return f'''schema_version = 1
[orchestration]
project_order = {order}
default_projects = ["demo"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"
[orchestration.project.demo]
{entry}
[cleanup]
release_tag_prefixes = []
keep_release_tags = []
ghcr_packages = []
ghcr_delete_packages = []
'''


def test_config_secret_and_runner_shapes_fail_with_policy_diagnostics(tmp_path, capsys):
    scalar = tmp_path / "secret.toml"; scalar.write_text("'scalar'\n")
    with pytest.raises(SystemExit):
        config._read_secret_document(scalar)
    scalar.write_text("[github]\ntoken = \"x\"\nother = \"y\"\n")
    with pytest.raises(SystemExit):
        config._read_secret_document(scalar)
    assert "unknown" in capsys.readouterr().out

    for raw in (
        {"build": {"commands": ["bad"], "quiet": True}},
        {"build": {"commands": [{"label": "x", "argv": ["echo"], "cwd": "."}], "quiet": True, "bake_set_vars": [1]}},
        {"build": {"commands": [{"label": "x", "argv": ["echo"], "cwd": "."}], "quiet": True, "login": {"registry": "", "username_env": "U", "token_env": "T", "required": True}}},
    ):
        with pytest.raises(SystemExit):
            config._validate_runner_steps(raw)


def test_config_project_shape_errors_are_reached_from_complete_documents(tmp_path, capsys):
    path = tmp_path / "cmru.toml"
    cases = (
        (project_toml().replace("description = \"demo project\"", "description = []"), "description"),
        (project_toml().replace("[project]\n", "[project]\nproject_metadata = []\n"), "unknown keys"),
        (project_toml().replace("build_step = \"build\"", "build_step = \"missing\""), "build_step"),
        (project_toml().replace("artifact_dirs = [\"dist\"]", "artifact_dirs = [\"../dist\"]"), "artifact_dirs"),
    )
    for raw, diagnostic in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
        assert diagnostic in capsys.readouterr().out


def test_config_orchestration_resolution_refuses_ambiguity_and_accepts_shared_facts(tmp_path):
    project = tmp_path / "demo"; project.mkdir()
    (project / "cmru.toml").write_text(project_toml(), encoding="utf-8")
    path = tmp_path / "cmru.orchestration.toml"
    for raw in (
        orch_toml(entry='config = "demo/cmru.toml"\ndepends_on = ["missing"]'),
        orch_toml(entry='config = "demo/cmru.toml"\ndepends_on = ["demo"]'),
        orch_toml(order='["other"]'),
    ):
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SystemExit):
            config.load_forge_config(path)
    path.write_text(orch_toml(), encoding="utf-8")
    loaded = config.load_forge_config(path)
    assert loaded.orchestration.project_configs["demo"] == (project / "cmru.toml").resolve()


def test_config_load_requires_orchestration_for_estate_operations(tmp_path):
    path = tmp_path / "cmru.toml"; path.write_text(project_toml(), encoding="utf-8")
    with pytest.raises(SystemExit):
        config.load_forge_config(path, require_orchestration=True)


def _project(*, strategy="scm", git_tag=True, prefix="demo-v", cwd="demo", base_version="1.0.0"):
    return SimpleNamespace(prefix=prefix, version=SimpleNamespace(strategy=strategy, base_version=base_version), git_tag=git_tag, cwd=cwd)


def test_version_status_covers_first_release_bump_override_set_and_counter(tmp_path, capsys):
    projects = {
        "first": _project(prefix="first-v"),
        "tagged": _project(prefix="tagged-v"),
        "counter": _project(strategy="counter", prefix="counter-v"),
    }
    changes = [
        ("first", projects["first"], None, "patch"),
        ("tagged", projects["tagged"], "tagged-v1.2.3", "patch"),
        ("counter", projects["counter"], None, "patch"),
    ]
    with patch.object(version, "detect_changed_projects", return_value=changes), patch.object(version, "_next_counter_version", return_value="1.0.0-r2"):
        version.status_cmd(tmp_path, projects, minor=True)
    output = capsys.readouterr().out
    assert "first-v0.1.0" in output
    assert "tagged-v1.3.0" in output
    assert "counter-v0.1.0" in output
    with patch.object(version, "detect_changed_projects", return_value=[("counter", projects["counter"], None, "patch")]), patch.object(version, "_next_counter_version", return_value="1.0.0-r2"):
        version.status_cmd(tmp_path, {"counter": projects["counter"]})
    assert "counter-v1.0.0-r2" in capsys.readouterr().out

    with patch.object(version, "detect_changed_projects", return_value=[("demo", _project(), None, "patch")]):
        version.status_cmd(tmp_path, {"demo": _project()}, set_version="9.9.9")
    assert "demo-v9.9.9" in capsys.readouterr().out


def test_version_release_orchestration_handles_no_tag_external_and_counter(tmp_path):
    root = repo(tmp_path)
    no_tag = _project(strategy="none", git_tag=False)
    external = _project(strategy="external:")
    with patch.object(version, "detect_changed_projects", return_value=[("plain", no_tag, None, "patch")]):
        assert version.release_cmd(root, {"plain": no_tag}) == []
    with patch.object(version, "detect_changed_projects", return_value=[("ext", external, None, "patch")]):
        with pytest.raises(SystemExit) as error:
            version.release_cmd(root, {"ext": external})
        assert error.value.code == 2

    counter = _project(strategy="counter", prefix="demo-v")
    with patch.object(version, "detect_changed_projects", return_value=[("demo", counter, None, "patch")]), patch.object(version, "_next_counter_version", return_value="1.0.0-r4"), patch.object(version, "_apply_strategy_counter", return_value="demo-v1.0.0-r4") as apply:
        assert version.release_cmd(root, {"demo": counter}, dry_run=True) == ["demo-v1.0.0-r4"]
    apply.assert_called_once_with(root, "demo-v", "1.0.0", dry_run=True)
